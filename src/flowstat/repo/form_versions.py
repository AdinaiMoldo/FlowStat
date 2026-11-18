"""A: the versions of the regional forms (US-55). A row is one file that was accepted."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from flowstat.model.form_versions import FormVersion, dump_findings, load_findings
from flowstat.model.rules import Finding

COLUMNS = (
    "region, quarter, number, sha256, size, rows, uploaded_by, uploaded_at, rules, findings, "
    "removed_at, removed_by"
)


class FormVersionNotFoundError(LookupError):
    pass


def _version(row: tuple[object, ...]) -> FormVersion:
    (region, quarter, number, sha, size, rows, by, at, rules, findings, removed_at, removed_by) = (
        row
    )
    return FormVersion(
        str(region), str(quarter), int(str(number)), str(sha), int(str(size)), int(str(rows)),
        str(by), str(at), str(rules), load_findings(str(findings)),
        None if removed_at is None else str(removed_at),
        None if removed_by is None else str(removed_by),
    )  # fmt: skip


class FormVersionRepo:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    def add(
        self,
        region: str,
        quarter: str,
        sha256: str,
        size: int,
        rows: int,
        by: str,
        at: str,
        rules: str,
        findings: Sequence[Finding],
        content: bytes,
    ) -> FormVersion:
        """Keep a file as the next version of the region and quarter. The number is taken in the
        same statement that inserts the row, so two files that arrive together get two numbers."""
        done = self.db.execute(
            "INSERT INTO form_versions (region, quarter, number, sha256, size, rows, uploaded_by,"
            " uploaded_at, rules, findings, content) VALUES (?, ?, (SELECT COALESCE(MAX(number), 0)"
            " + 1 FROM form_versions WHERE region = ? AND quarter = ?), ?, ?, ?, ?, ?, ?, ?, ?)",
            (region, quarter, region, quarter, sha256, size, rows, by, at, rules,
             dump_findings(findings), content),
        )  # fmt: skip
        row = self.db.execute(
            f"SELECT {COLUMNS} FROM form_versions WHERE rowid = ?", (done.lastrowid,)
        ).fetchone()
        return _version(row)

    def all(self, region: str, quarter: str) -> list[FormVersion]:
        """Every version of the region and quarter, the newest first."""
        rows = self.db.execute(
            f"SELECT {COLUMNS} FROM form_versions WHERE region = ? AND quarter = ?"
            " ORDER BY number DESC",
            (region, quarter),
        ).fetchall()
        return [_version(r) for r in rows]

    def count(self, region: str, quarter: str) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) FROM form_versions WHERE region = ? AND quarter = ?", (region, quarter)
        ).fetchone()
        return int(row[0])

    def get(self, region: str, quarter: str, number: int) -> FormVersion:
        row = self.db.execute(
            f"SELECT {COLUMNS} FROM form_versions WHERE region = ? AND quarter = ? AND number = ?",
            (region, quarter, number),
        ).fetchone()
        if row is None:
            raise FormVersionNotFoundError(f"{region} {quarter} v{number}")
        return _version(row)

    def current(self, region: str, quarter: str) -> FormVersion | None:
        """The latest version, unless it was removed."""
        found = self.all(region, quarter)[:1]
        return found[0] if found and found[0].removed_at is None else None

    def content(self, region: str, quarter: str, number: int) -> bytes:
        row = self.db.execute(
            "SELECT content FROM form_versions WHERE region = ? AND quarter = ? AND number = ?",
            (region, quarter, number),
        ).fetchone()
        if row is None:
            raise FormVersionNotFoundError(f"{region} {quarter} v{number}")
        return bytes(row[0])

    def mark_removed(self, region: str, quarter: str, at: str, by: str) -> bool:
        """The current version was removed; the file is gone and the history stays."""
        current = self.current(region, quarter)
        if current is None:
            return False
        self.db.execute(
            "UPDATE form_versions SET removed_at = ?, removed_by = ? WHERE region = ? AND"
            " quarter = ? AND number = ? AND removed_at IS NULL",
            (at, by, region, quarter, current.number),
        )
        return True
