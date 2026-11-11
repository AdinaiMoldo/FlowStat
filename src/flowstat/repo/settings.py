"""A few named values a service keeps in its own database (US-40: the ingest key it was given)."""

from __future__ import annotations

import sqlite3


class SettingStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    def get(self, name: str) -> str | None:
        row = self.db.execute("SELECT value FROM service_settings WHERE name = ?", (name,))
        found = row.fetchone()
        return None if found is None else str(found[0])

    def set(self, name: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO service_settings (name, value) VALUES (?, ?)"
            " ON CONFLICT (name) DO UPDATE SET value = excluded.value",
            (name, value),
        )
