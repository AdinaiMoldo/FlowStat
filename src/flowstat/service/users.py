"""Create and authenticate users (A3, docs/api-spec.md section 2).

The token itself comes from the caller: this layer never reads a source of randomness (the edge,
the command line, does). Only the SHA-256 of a token is stored, so a copy of the database does not
give the tokens.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Iterable

from flowstat.repo.users import User, UserStore

ROLES = ("admin", "designer", "operator")
MIN_TOKEN_LENGTH = 32
_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf8")).hexdigest()


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

    def authenticate(self, token: str) -> User | None:
        """The user a token belongs to, or None (unknown, empty or disabled)."""
        if not token:
            return None
        # The lookup is by the SHA-256 of the token, so how long it takes says nothing about the
        # token itself; there is no comparison of secrets that a timing attack could use.
        return self.store.by_token_hash(hash_token(token))

    def disable(self, name: str) -> None:
        self.store.disable(name)

    def all(self) -> list[User]:
        return self.store.all()
