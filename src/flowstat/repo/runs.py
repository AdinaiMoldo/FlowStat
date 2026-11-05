"""A2: runs and their history. The history is append-only and is the single record of a run."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from flowstat.model.events import Event, EventKind
from flowstat.model.states import RunOutcome, StepState


class RunNotFoundError(LookupError):
    pass


class EventConflictError(RuntimeError):
    """An event does not fit the stored history (a used sequence number, or a gap)."""


@dataclass(frozen=True, slots=True)
class StoredRun:
    run_id: str
    workflow: str
    version: int
    inputs: dict[str, object]
    created_at: str


_EVENT_COLUMNS = "seq, kind, at, step, state, outcome, code, message, output, attempt, delay"


def _event(row: tuple[object, ...]) -> Event:
    seq, kind, at, step, state, outcome, code, message, output, attempt, delay = row
    return Event(
        int(seq),  # type: ignore[call-overload]
        EventKind(kind),
        float(at),  # type: ignore[arg-type]
        step,  # type: ignore[arg-type]
        StepState(state) if state is not None else None,
        RunOutcome(outcome) if outcome is not None else None,
        code,  # type: ignore[arg-type]
        message,  # type: ignore[arg-type]
        json.loads(output) if output is not None else None,  # type: ignore[arg-type]
        attempt,  # type: ignore[arg-type]
        delay,  # type: ignore[arg-type]
    )


def _values(run_id: str, e: Event) -> tuple[object, ...]:
    return (
        run_id,
        e.seq,
        e.kind.value,
        e.at,
        e.step,
        e.state.value if e.state is not None else None,
        e.outcome.value if e.outcome is not None else None,
        e.code,
        e.message,
        json.dumps(e.output, sort_keys=True) if e.output is not None else None,
        e.attempt,
        e.delay,
    )


class RunStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    # -- runs ----------------------------------------------------------------------------------
    def create(
        self, run_id: str, workflow: str, version: int, inputs: dict[str, object], now: str
    ) -> None:
        self.db.execute(
            "INSERT INTO runs (run_id, workflow, version, inputs, created_at) VALUES (?,?,?,?,?)",
            (run_id, workflow, version, json.dumps(inputs, sort_keys=True), now),
        )

    def get(self, run_id: str) -> StoredRun:
        row = self.db.execute(
            "SELECT run_id, workflow, version, inputs, created_at FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise RunNotFoundError(run_id)
        return StoredRun(row[0], row[1], row[2], json.loads(row[3]), row[4])

    def runs(self) -> list[StoredRun]:
        return self._list("", ())

    def runs_of(self, workflow: str) -> list[StoredRun]:
        return self._list("WHERE workflow = ?", (workflow,))

    def _list(self, where: str, args: tuple[object, ...]) -> list[StoredRun]:
        rows = self.db.execute(
            "SELECT run_id, workflow, version, inputs, created_at FROM runs "
            f"{where} ORDER BY created_at, run_id",
            args,
        ).fetchall()
        return [StoredRun(r[0], r[1], r[2], json.loads(r[3]), r[4]) for r in rows]

    # -- history -------------------------------------------------------------------------------
    def events(self, run_id: str) -> list[Event]:
        self.get(run_id)
        rows = self.db.execute(
            f"SELECT {_EVENT_COLUMNS} FROM run_events WHERE run_id = ? ORDER BY seq", (run_id,)
        ).fetchall()
        return [_event(r) for r in rows]

    def append(self, run_id: str, events: Iterable[Event]) -> None:
        """Append events in one transaction.

        Delivery is at least once: an event that is already stored, identical, is ignored. A
        different event with a used sequence number, or a gap in the sequence, refuses the whole
        batch and stores nothing.
        """
        self.get(run_id)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT COALESCE(MAX(seq), -1) FROM run_events WHERE run_id = ?", (run_id,)
            ).fetchone()
            last = int(row[0])
            for event in events:
                if event.seq <= last:
                    stored = self.db.execute(
                        f"SELECT {_EVENT_COLUMNS} FROM run_events WHERE run_id = ? AND seq = ?",
                        (run_id, event.seq),
                    ).fetchone()
                    if _event(stored) != event:
                        raise EventConflictError(f"{run_id}: event {event.seq} differs")
                    continue
                if event.seq != last + 1:
                    raise EventConflictError(
                        f"{run_id}: expected event {last + 1}, got {event.seq}"
                    )
                self.db.execute(
                    f"INSERT INTO run_events (run_id, {_EVENT_COLUMNS}) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    _values(run_id, event),
                )
                last = event.seq
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")
