"""The runs of a campaign and the results of the steps that only compute (US-58)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CampaignRun:
    run_id: str
    campaign_id: str
    started_by: str
    started_at: str
    forms: dict[str, int]  # region -> the version of its form when the run was started


@dataclass(frozen=True, slots=True)
class Use:
    step: str
    reused: bool
    from_run: str | None


class ReuseRepo:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    # -- the runs of a campaign -------------------------------------------------------------------
    def add_run(
        self, run_id: str, campaign_id: str, by: str, now: str, forms: dict[str, int]
    ) -> None:
        self.db.execute(
            "INSERT INTO campaign_runs (run_id, campaign_id, started_by, started_at, forms)"
            " VALUES (?, ?, ?, ?, ?)",
            (run_id, campaign_id, by, now, json.dumps(forms, sort_keys=True)),
        )

    def runs_of(self, campaign_id: str) -> list[CampaignRun]:
        """The runs of a campaign, the newest first."""
        rows = self.db.execute(
            "SELECT run_id, campaign_id, started_by, started_at, forms FROM campaign_runs"
            " WHERE campaign_id = ? ORDER BY rowid DESC",
            (campaign_id,),
        ).fetchall()
        return [
            CampaignRun(str(a), str(b), str(c), str(d), json.loads(e)) for a, b, c, d, e in rows
        ]

    def is_campaign_run(self, run_id: str) -> bool:
        row = self.db.execute("SELECT 1 FROM campaign_runs WHERE run_id = ?", (run_id,)).fetchone()
        return row is not None

    # -- the results that were kept ---------------------------------------------------------------
    def kept(self, key: str) -> tuple[dict[str, object], str] | None:
        """The output that was kept under the key and the run that made it, or None."""
        row = self.db.execute(
            "SELECT output, run_id FROM step_results WHERE key = ?", (key,)
        ).fetchone()
        return None if row is None else (json.loads(row[0]), str(row[1]))

    def keep(
        self, key: str, action: str, output: dict[str, object], run_id: str, step: str, now: str
    ) -> None:
        """Keep a result; the first one under a key stays (two runs may compute the same)."""
        self.db.execute(
            "INSERT OR IGNORE INTO step_results (key, action, output, run_id, step, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (key, action, json.dumps(output, sort_keys=True), run_id, step, now),
        )

    def note(self, run_id: str, step: str, reused: bool, from_run: str | None) -> None:
        """What happened to a step of a run; a retry of the step writes over the first note."""
        self.db.execute(
            "INSERT OR REPLACE INTO step_uses (run_id, step, reused, from_run) VALUES (?, ?, ?, ?)",
            (run_id, step, int(reused), from_run),
        )

    def uses(self, run_id: str) -> list[Use]:
        rows = self.db.execute(
            "SELECT step, reused, from_run FROM step_uses WHERE run_id = ? ORDER BY step",
            (run_id,),
        ).fetchall()
        return [Use(str(s), bool(r), None if f is None else str(f)) for s, r, f in rows]
