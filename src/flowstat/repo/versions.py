"""A1: workflow versions. A draft can be edited; a published version never changes."""

from __future__ import annotations

import sqlite3

from flowstat.model.versions import Version, VersionStatus


class VersionNotFoundError(LookupError):
    pass


class AlreadyPublishedError(RuntimeError):
    pass


_COLUMNS = "name, number, status, text, catalogue, created_at, published_at"


def _row(row: tuple[str, int, str, str, str, str, str | None]) -> Version:
    name, number, status, text, catalogue, created_at, published_at = row
    return Version(name, number, VersionStatus(status), text, catalogue, created_at, published_at)


class VersionStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    def get(self, name: str, number: int) -> Version:
        row = self.db.execute(
            f"SELECT {_COLUMNS} FROM versions WHERE name = ? AND number = ?", (name, number)
        ).fetchone()
        if row is None:
            raise VersionNotFoundError(f"{name} v{number}")
        return _row(row)

    def versions(self, name: str) -> list[Version]:
        rows = self.db.execute(
            f"SELECT {_COLUMNS} FROM versions WHERE name = ? ORDER BY number", (name,)
        ).fetchall()
        return [_row(r) for r in rows]

    def latest(self, name: str) -> Version | None:
        found = self.versions(name)
        return found[-1] if found else None

    def names(self) -> list[str]:
        rows = self.db.execute("SELECT DISTINCT name FROM versions ORDER BY name").fetchall()
        return [r[0] for r in rows]

    def save(self, name: str, text: str, catalogue: str, now: str) -> Version:
        """Save edits: update the open draft, or create the next version as a new draft."""
        latest = self.latest(name)
        if latest is not None and latest.status is VersionStatus.DRAFT:
            self.db.execute(
                "UPDATE versions SET text = ?, catalogue = ? WHERE name = ? AND number = ?",
                (text, catalogue, name, latest.number),
            )
            return self.get(name, latest.number)
        number = 1 if latest is None else latest.number + 1
        self.db.execute(
            f"INSERT INTO versions ({_COLUMNS}) VALUES (?, ?, 'draft', ?, ?, ?, NULL)",
            (name, number, text, catalogue, now),
        )
        return self.get(name, number)

    def publish(self, name: str, number: int, catalogue: str, now: str) -> Version:
        """Freeze a draft, together with the catalogue snapshot it was compiled against."""
        current = self.get(name, number)
        if current.status is VersionStatus.PUBLISHED:
            raise AlreadyPublishedError(f"{name} v{number} is already published")
        self.db.execute(
            "UPDATE versions SET status = 'published', catalogue = ?, published_at = ? "
            "WHERE name = ? AND number = ?",
            (catalogue, now, name, number),
        )
        return self.get(name, number)

    def import_published(self, version: Version) -> None:
        """Store a version that was published elsewhere, with its number (a deployed service).

        The row is already published, so it can never be edited; an existing number is refused.
        """
        if version.status is not VersionStatus.PUBLISHED or version.published_at is None:
            raise ValueError("only a published version can be imported")
        self.db.execute(
            f"INSERT INTO versions ({_COLUMNS}) VALUES (?, ?, 'published', ?, ?, ?, ?)",
            (
                version.name,
                version.number,
                version.text,
                version.catalogue,
                version.created_at,
                version.published_at,
            ),
        )
