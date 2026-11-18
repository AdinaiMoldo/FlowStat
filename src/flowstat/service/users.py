"""Create and authenticate users (A3, docs/api-spec.md section 2).

The token itself comes from the caller: this layer never reads a source of randomness (the edge,
the command line, does). Only the SHA-256 of a token is stored, so a copy of the database does not
give the tokens.

A person can also sign in with a name and a password (US-46). The password is stored as a salted
scrypt hash (the salt comes from the caller, like a token); a successful sign-in makes a session
token, which is a token like the others (only its SHA-256 is stored) and which expires.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from flowstat.repo.users import User, UserStore

ROLES = ("admin", "designer", "operator")
MIN_TOKEN_LENGTH = 32
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128  # a longer one would only make the check slow for nobody's good
SESSION_HOURS = 8
# scrypt: memory 128 * N * r = 16 MiB, about 50 ms; the parameters are stored with each hash, so
# they can be raised later without locking anybody out.
_N, _R, _P = 2**14, 8, 1
_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf8")).hexdigest()


def hash_password(password: str, salt: bytes) -> str:
    """``scrypt$N$r$p$salt$hash``, all in hex or decimal text."""
    key = hashlib.scrypt(
        password.encode("utf8"), salt=salt, n=_N, r=_R, p=_P, maxmem=64 * 1024 * 1024
    )
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${key.hex()}"


def verify_password(stored: str, password: str) -> bool:
    """Whether ``password`` is the one that ``stored`` was made from (constant-time compare)."""
    try:
        kind, n, r, p, salt, key = stored.split("$")
        if kind != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf8"),
            salt=bytes.fromhex(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=64 * 1024 * 1024,
        )
        return hmac.compare_digest(derived.hex(), key)
    except (ValueError, TypeError):
        return False


_DUMMY: list[str] = []


def _dummy() -> str:
    """A hash to check against when the name is unknown, so that an unknown name and a wrong
    password take the same time."""
    if not _DUMMY:
        _DUMMY.append(hash_password("no such user", b"\x00" * 16))
    return _DUMMY[0]


def later(now: str, hours: int) -> str:
    """``now`` (ISO 8601 with Z) plus some hours, in the same format."""
    moment = datetime.fromisoformat(now.replace("Z", "+00:00"))
    return (moment + timedelta(hours=hours)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class UserService:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.store = UserStore(db)

    def add(self, name: str, role: str, titles: Iterable[str], token: str, now: str) -> User:
        if not _NAME.match(name):
            raise ValueError("a user name is lower case letters, digits, '_', '-' or '.'")
        if role not in ROLES:
            raise ValueError(f"the role must be one of {', '.join(ROLES)}")
        if len(token) < MIN_TOKEN_LENGTH:
            raise ValueError(f"a token has at least {MIN_TOKEN_LENGTH} characters")
        clean = frozenset(titles)
        if not all(_NAME.match(t) for t in clean):
            raise ValueError("a title is lower case letters, digits, '_', '-' or '.'")
        return self.store.add(name, role, clean, hash_token(token), now)

    def authenticate(self, token: str, now: str | None = None) -> User | None:
        """The user a token belongs to, or None (unknown, empty or disabled).

        With ``now`` the token of a session that has not expired counts too; without it only the
        tokens made by ``add`` do (the service of a deployment has no sessions).
        """
        if not token:
            return None
        # The lookup is by the SHA-256 of the token, so how long it takes says nothing about the
        # token itself; there is no comparison of secrets that a timing attack could use.
        digest = hash_token(token)
        user = self.store.by_token_hash(digest)
        if user is None and now is not None:
            user = self.store.by_session_hash(digest, now)
        return user

    def set_password(self, name: str, password: str, salt: bytes) -> None:
        if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
            raise ValueError(
                f"a password has {MIN_PASSWORD_LENGTH} to {MAX_PASSWORD_LENGTH} characters"
            )
        if len(salt) < 16:
            raise ValueError("the salt has at least 16 bytes")
        self.store.set_password_hash(name, hash_password(password, salt))

    def login(self, name: str, password: str, session_token: str, now: str) -> User | None:
        """Check a name and a password; on success make a session that expires and return the
        user. A wrong name, a wrong password, a disabled user and a user with no password all
        give None, after the same work."""
        found = self.store.for_login(name) if len(password) <= MAX_PASSWORD_LENGTH else None
        stored = found[1] if found else _dummy()
        good = verify_password(stored, password)
        if not (found and good):
            return None
        if len(session_token) < MIN_TOKEN_LENGTH:
            raise ValueError(f"a token has at least {MIN_TOKEN_LENGTH} characters")
        self.store.prune_sessions(now)
        self.store.add_session(
            hash_token(session_token), found[0].name, now, later(now, SESSION_HOURS)
        )
        return found[0]

    def logout(self, token: str) -> None:
        """End the session of ``token``; a token that is not a session is left as it is."""
        if token:
            self.store.remove_session(hash_token(token))

    def disable(self, name: str) -> None:
        self.store.disable(name)

    def all(self) -> list[User]:
        return self.store.all()
