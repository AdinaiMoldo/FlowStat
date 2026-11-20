"""The results of the campaigns (US-57): written once, marked stale once, never changed."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal

from flowstat.model.consolidation import Left, Line, Result


class ConsolidationNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class Stored:
    campaign_id: str
    number: int  # 1, 2, 3 ... for a campaign
    result: Result
    made_by: str
    made_at: str
    stale_at: str | None = None
    stale_reason: str | None = None


def dump(result: Result) -> str:
    return json.dumps(
        {
            "included": [list(item) for item in result.included],
            "left_out": [[o.region, o.reason, o.detail] for o in result.left_out],
            "lines": [
                [ln.code, ln.name, str(ln.total), [[r, v, str(x)] for r, v, x in ln.parts]]
                for ln in result.lines
            ],
        },
        ensure_ascii=False,
    )


def load(kind: str, complete: bool, expected: int, text: str) -> Result:
    body = json.loads(text)
    return Result(
        kind,
        complete,
        expected,
        tuple((str(r), int(v), str(s)) for r, v, s in body["included"]),
        tuple(Left(str(r), str(why), str(detail)) for r, why, detail in body["left_out"]),
        tuple(
            Line(str(code), str(name), Decimal(total),
                 tuple((str(r), int(v), Decimal(x)) for r, v, x in parts))
            for code, name, total, parts in body["lines"]
        ),
    )  # fmt: skip


COLUMNS = (
    "campaign_id, number, kind, complete, expected, body, made_by, made_at, stale_at, stale_reason"
)


def _stored(row: tuple[object, ...]) -> Stored:
    campaign_id, number, kind, complete, expected, body, by, at, stale_at, stale_reason = row
    return Stored(
        str(campaign_id),
        int(str(number)),
        load(str(kind), bool(complete), int(str(expected)), str(body)),
        str(by),
        str(at),
        None if stale_at is None else str(stale_at),
        None if stale_reason is None else str(stale_reason),
    )


class ConsolidationRepo:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    def add(self, campaign_id: str, result: Result, by: str, now: str) -> Stored:
        """Keep a result as the next number of its campaign."""
        row = self.db.execute(
            "SELECT COALESCE(MAX(number), 0) + 1 FROM consolidations WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        number = int(row[0])
        self.db.execute(
            f"INSERT INTO consolidations ({COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
            (campaign_id, number, result.kind, int(result.complete), result.expected,
             dump(result), by, now),
        )  # fmt: skip
        return Stored(campaign_id, number, result, by, now)

    def all(self, campaign_id: str) -> list[Stored]:
        """The results of a campaign, the newest first."""
        rows = self.db.execute(
            f"SELECT {COLUMNS} FROM consolidations WHERE campaign_id = ? ORDER BY number DESC",
            (campaign_id,),
        ).fetchall()
        return [_stored(r) for r in rows]

    def get(self, campaign_id: str, number: int) -> Stored:
        row = self.db.execute(
            f"SELECT {COLUMNS} FROM consolidations WHERE campaign_id = ? AND number = ?",
            (campaign_id, number),
        ).fetchone()
        if row is None:
            raise ConsolidationNotFoundError(f"{campaign_id} #{number}")
        return _stored(row)

    def mark_stale(self, campaign_id: str, now: str, reason: str) -> int:
        """Every result of the campaign that is not stale yet becomes stale. How many were."""
        done = self.db.execute(
            "UPDATE consolidations SET stale_at = ?, stale_reason = ?"
            " WHERE campaign_id = ? AND stale_at IS NULL",
            (now, reason, campaign_id),
        )
        return int(done.rowcount)
