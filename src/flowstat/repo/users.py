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

    def hash_of(self, name: str) -> str | None:
        """The token hash of a user (the service compares it with the key the core announces)."""
        row = self.db.execute("SELECT token_hash FROM users WHERE name = ?", (name,)).fetchone()
        return None if row is None else str(row[0])

    def exists(self, name: str) -> bool:
        return self.hash_of(name) is not None

    def replace_token(self, name: str, token_hash: str) -> None:
        """Give ``name`` another token hash. Raises ``UserExistsError`` when another user has it."""
        try:
            cursor = self.db.execute(
                "UPDATE users SET token_hash = ?, disabled = 0 WHERE name = ?", (token_hash, name)
            )
        except sqlite3.IntegrityError:
            raise UserExistsError("another user already has this token") from None
        if cursor.rowcount == 0:
            raise UserNotFoundError(f"there is no user '{name}'")

    def remove(self, name: str) -> None:
        """Delete a user (only the key of a rotation that did not happen is removed this way)."""
        self.db.execute("DELETE FROM users WHERE name = ?", (name,))

    def disable(self, name: str) -> None:
        cursor = self.db.execute("UPDATE users SET disabled = 1 WHERE name = ?", (name,))
        if cursor.rowcount == 0:
            raise UserNotFoundError(f"there is no user '{name}'")

    # -- passwords and sessions (US-46) ----------------------------------------------------
    def set_password_hash(self, name: str, password_hash: str) -> None:
        cursor = self.db.execute(
            "UPDATE users SET password_hash = ? WHERE name = ?", (password_hash, name)
        )
        if cursor.rowcount == 0:
            raise UserNotFoundError(f"there is no user '{name}'")

    def for_login(self, name: str) -> tuple[User, str] | None:
        """The enabled user ``name`` and the hash of its password, or None (unknown, disabled,
        or no password set)."""
        row = self.db.execute(
            "SELECT name, role, titles, disabled, password_hash FROM users"
            " WHERE name = ? AND disabled = 0 AND password_hash IS NOT NULL",
            (name,),
        ).fetchone()
        return None if row is None else (_user(row[:4]), str(row[4]))

    def add_session(self, token_hash: str, name: str, now: str, expires_at: str) -> None:
        self.db.execute(
            "INSERT INTO sessions (token_hash, user_name, created_at, expires_at)"
            " VALUES (?, ?, ?, ?)",
            (token_hash, name, now, expires_at),
        )

    def by_session_hash(self, token_hash: str, now: str) -> User | None:
        """The enabled user of a session that has not expired, or None."""
        row = self.db.execute(
            "SELECT u.name, u.role, u.titles, u.disabled FROM sessions s"
            " JOIN users u ON u.name = s.user_name"
            " WHERE s.token_hash = ? AND s.expires_at > ? AND u.disabled = 0",
            (token_hash, now),
        ).fetchone()
        return None if row is None else _user(row)

    def remove_session(self, token_hash: str) -> None:
        self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def prune_sessions(self, now: str) -> None:
        self.db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))

    def all(self) -> list[User]:
        rows = self.db.execute("SELECT name, role, titles, disabled FROM users ORDER BY name")
        return [_user(row) for row in rows]
