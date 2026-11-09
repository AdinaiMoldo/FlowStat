"""A6: the position of a service in the history of its runs, for shipping them to the core.

The log of the service (``run_events``) is the source of truth; this is only how far the core has
acknowledged, per run. It moves forward when the core stores a batch and back when the core says
it has less (it lost data), so the log is never copied and never disagrees with itself.
"""

from __future__ import annotations

import sqlite3

from flowstat.model.events import Event
from flowstat.repo.runs import _EVENT_COLUMNS, StoredRun, _event, _stored


class OutboxStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    def shipped_through(self, run_id: str) -> int:
        """The number of the last event the core has acknowledged for the run, or -1."""
        row = self.db.execute(
            "SELECT shipped_through FROM outbox WHERE run_id = ?", (run_id,)
        ).fetchone()
        return int(row[0]) if row is not None else -1

    def set(self, run_id: str, through: int, allow_back: bool = False) -> None:
        """Record the position. A lower one is refused unless the core said it has less."""
        if not allow_back:
            through = max(through, self.shipped_through(run_id))
        self.db.execute(
            "INSERT INTO outbox (run_id, shipped_through) VALUES (?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET shipped_through = excluded.shipped_through",
            (run_id, through),
        )

    def pending(self) -> list[StoredRun]:
        """The runs that have events after their position, oldest first."""
        rows = self.db.execute(
            "SELECT r.run_id, r.workflow, r.version, r.inputs, r.created_at, r.origin "
            "FROM runs r LEFT JOIN outbox o ON o.run_id = r.run_id "
            "WHERE EXISTS (SELECT 1 FROM run_events e WHERE e.run_id = r.run_id "
            "AND e.seq > COALESCE(o.shipped_through, -1)) "
            "AND NOT EXISTS (SELECT 1 FROM ownership w WHERE w.run_id = r.run_id "
            "AND w.state != 'OWNED') "  # a run that is leaving, or has left, is not ours to ship
            "ORDER BY r.created_at, r.run_id"
        ).fetchall()
        return [_stored(r) for r in rows]

    def events_after(self, run_id: str, seq: int, limit: int) -> list[Event]:
        rows = self.db.execute(
            f"SELECT {_EVENT_COLUMNS} FROM run_events WHERE run_id = ? AND seq > ? "
            "ORDER BY seq LIMIT ?",
            (run_id, seq, limit),
        ).fetchall()
        return [_event(r) for r in rows]
