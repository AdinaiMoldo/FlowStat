"""Users, their role and their titles (A3). A token is never stored, only its hash."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


class UserExistsError(ValueError):
    """The name or the token hash is already taken."""


class UserNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class User:
    name: str
    role: str  # admin, designer or operator
    titles: frozenset[str]  # for example chief_statistician: who may answer an approval
    disabled: bool = False


def _user(row: tuple[str, str, str, int]) -> User:
    name, role, titles, disabled = row
    return User(name, role, frozenset(json.loads(titles)), bool(disabled))


class UserStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    def add(self, name: str, role: str, titles: frozenset[str], token_hash: str, now: str) -> User:
        try:
            self.db.execute(
                "INSERT INTO users (name, role, titles, token_hash, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (name, role, json.dumps(sorted(titles)), token_hash, now),
            )
        except sqlite3.IntegrityError as error:
            if "UNIQUE" in str(error) or "PRIMARY" in str(error):
                raise UserExistsError(f"the user '{name}' or its token already exists") from error
            raise
        return User(name, role, titles)

    def by_token_hash(self, token_hash: str) -> User | None:
        """The enabled user with this token hash, or None."""
        row = self.db.execute(
            "SELECT name, role, titles, disabled FROM users WHERE token_hash = ? AND disabled = 0",
            (token_hash,),
        ).fetchone()
        return None if row is None else _user(row)

    def disable(self, name: str) -> None:
        cursor = self.db.execute("UPDATE users SET disabled = 1 WHERE name = ?", (name,))
        if cursor.rowcount == 0:
            raise UserNotFoundError(f"there is no user '{name}'")

    def all(self) -> list[User]:
        rows = self.db.execute("SELECT name, role, titles, disabled FROM users ORDER BY name")
        return [_user(row) for row in rows]
