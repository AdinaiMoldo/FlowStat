"""The keys of a deployed service that the core can replace (US-40, docs/api-spec.md section 7).

Two keys can be rotated without deploying again. The *control key* is presented by the core, and
the service holds its hash: a rotation puts a second control user beside the first (``prepare``),
and ``settle`` makes the key the caller holds the only one. The *ingest key* is presented by the
service, and the service holds the key: the core gives it the new one (``store_ingest``) and the
service uses it from its next send. Every call can be made again with the same effect, which is
what lets the core finish a rotation that a stop interrupted.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from flowstat.repo.settings import SettingStore
from flowstat.repo.users import UserStore
from flowstat.service.users import MIN_TOKEN_LENGTH, hash_token

CONTROL = "control"  # the user of the key the core presents to move runs in and out
NEXT = "control.next"  # the second control user while a rotation is going on
INGEST = "ingest_key"  # the setting that holds the ingest key the service was given


class KeysError(ValueError):
    """The request cannot be done in the state the keys are in."""


@dataclass(frozen=True, slots=True)
class KeyState:
    caller: str  # which control user the caller is
    control_next: bool  # a second control key is accepted (a rotation is half done)
    ingest_sha256: str | None  # the hash of the key the service ships with, None: it has none


class ServiceKeys:
    def __init__(self, db: sqlite3.Connection, env_ingest_key: str | None = None) -> None:
        self.db = db
        self.users = UserStore(db)
        self.settings = SettingStore(db)
        self.env_ingest_key = env_ingest_key  # the key the process was started with, if any

    def state(self, caller: str) -> KeyState:
        key = self.ingest_key()
        return KeyState(caller, self.users.exists(NEXT), hash_token(key) if key else None)

    def ingest_key(self) -> str | None:
        """The key the service ships with: the last one the core gave it, else the one of its
        environment (so a restart keeps the new key)."""
        return self.settings.get(INGEST) or self.env_ingest_key or None

    def prepare(self, caller: str, sha256: str, now: str) -> None:
        """Accept a second control key (its hash) beside the first. Only the first may ask."""
        if caller != CONTROL:
            raise KeysError("only the current control key may announce the next one")
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise KeysError("the key is announced by its SHA-256 (64 hexadecimal digits)")
        if sha256 == self.users.hash_of(CONTROL):
            raise KeysError("the next control key is the current one")
        if self.users.exists(NEXT):
            self.users.replace_token(NEXT, sha256)  # an earlier attempt that was not settled
        else:
            self.users.add(NEXT, "admin", frozenset(), sha256, now)

    def settle(self, caller: str) -> None:
        """The key the caller holds becomes the only control key. A caller that holds the next
        key moves it into place; one that holds the current key drops the next (it is the
        rollback). Called again with the same key it changes nothing."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if caller == NEXT:
                moved = self.users.hash_of(NEXT)
                if moved is None:
                    raise KeysError("there is no next control key")
                self.users.remove(NEXT)  # its hash is unique, so it goes before it is moved
                self.users.replace_token(CONTROL, moved)
            else:
                self.users.remove(NEXT)
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    def store_ingest(self, key: str) -> None:
        if len(key) < MIN_TOKEN_LENGTH:
            raise KeysError(f"a key has at least {MIN_TOKEN_LENGTH} characters")
        self.settings.set(INGEST, key)
