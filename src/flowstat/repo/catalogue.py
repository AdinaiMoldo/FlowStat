"""A4: the catalogue (lists of items such as the regional offices)."""

from __future__ import annotations

import json
import sqlite3


class InvalidCatalogueError(ValueError):
    pass


class CatalogueRepo:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    def set_list(self, list_name: str, items: list[dict[str, str]]) -> None:
        """Replace a list. Every item needs a unique ``code``; nothing changes on an error."""
        seen: set[str] = set()
        for item in items:
            code = item.get("code")
            if not code:
                raise InvalidCatalogueError(f"an item of '{list_name}' has no code")
            if code in seen:
                raise InvalidCatalogueError(f"duplicate code '{code}' in '{list_name}'")
            seen.add(code)
        self.db.execute("BEGIN")
        try:
            self.db.execute("DELETE FROM catalogue_items WHERE list_name = ?", (list_name,))
            self.db.executemany(
                "INSERT INTO catalogue_items (list_name, position, code, attrs) "
                "VALUES (?, ?, ?, ?)",
                [
                    (list_name, i, item["code"], json.dumps(item, sort_keys=True))
                    for i, item in enumerate(items)
                ],
            )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def get_list(self, list_name: str) -> list[dict[str, str]] | None:
        rows = self.db.execute(
            "SELECT attrs FROM catalogue_items WHERE list_name = ? ORDER BY position", (list_name,)
        ).fetchall()
        return [json.loads(r[0]) for r in rows] if rows else None

    def all_lists(self) -> dict[str, list[dict[str, str]]]:
        names = self.db.execute(
            "SELECT DISTINCT list_name FROM catalogue_items ORDER BY list_name"
        ).fetchall()
        return {n[0]: self.get_list(n[0]) or [] for n in names}
