"""A2: runs and their history. The history is append-only and is the single record of a run."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TypeVar

from flowstat.model.events import Event, EventKind
from flowstat.model.states import RunOutcome, StepState

T = TypeVar("T")


class RunNotFoundError(LookupError):
    pass


class EventConflictError(RuntimeError):
    """An event does not fit the stored history (a used sequence number, or a gap)."""

    def __init__(self, message: str, expected: int | None = None) -> None:
        super().__init__(message)
        self.expected = expected  # the sequence number the history needs next, when it is a gap


class RunConflictError(RuntimeError):
    """A run with this id exists and is not the one that was described."""


@dataclass(frozen=True, slots=True)
class StoredRun:
    run_id: str
    workflow: str
    version: int
    inputs: dict[str, object]
    created_at: str
    origin: str | None = None  # None: a run of this process; else the deployment that shipped it


_RUN_COLUMNS = "run_id, workflow, version, inputs, created_at, origin"


def _stored(row: tuple[object, ...]) -> StoredRun:
    return StoredRun(row[0], row[1], row[2], json.loads(row[3]), row[4], row[5])  # type: ignore[arg-type]


_EVENT_COLUMNS = (
    "seq, kind, at, step, state, outcome, code, message, output, attempt, delay, decision"
)


def _event(row: tuple[object, ...]) -> Event:
    seq, kind, at, step, state, outcome, code, message, output, attempt, delay, decision = row
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
        decision,  # type: ignore[arg-type]
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
        e.decision,
    )


class RunStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    # -- runs ----------------------------------------------------------------------------------
    def create(
        self,
        run_id: str,
        workflow: str,
        version: int,
        inputs: dict[str, object],
        now: str,
        origin: str | None = None,
    ) -> None:
        self.db.execute(
            "INSERT INTO runs (run_id, workflow, version, inputs, created_at, origin) "
            "VALUES (?,?,?,?,?,?)",
            (run_id, workflow, version, json.dumps(inputs, sort_keys=True), now, origin),
        )

    def ensure(
        self,
        run_id: str,
        workflow: str,
        version: int,
        inputs: dict[str, object],
        now: str,
        origin: str | None,
    ) -> bool:
        """Make the run if it is not there; True when it was made. The same run again changes
        nothing; a run with this id that differs (in workflow, version, inputs or origin) is a
        RunConflictError."""
        made = self.db.execute(
            "INSERT OR IGNORE INTO runs (run_id, workflow, version, inputs, created_at, origin) "
            "VALUES (?,?,?,?,?,?)",
            (run_id, workflow, version, json.dumps(inputs, sort_keys=True), now, origin),
        ).rowcount
        if made:
            return True
        kept = self.get(run_id)
        if (kept.workflow, kept.version, kept.inputs, kept.origin) != (
            workflow,
            version,
            inputs,
            origin,
        ):
            raise RunConflictError(f"{run_id}: a different run has this id")
        return False

    def get(self, run_id: str) -> StoredRun:
        row = self.db.execute(
            f"SELECT {_RUN_COLUMNS} FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise RunNotFoundError(run_id)
        return _stored(row)

    def runs(self) -> list[StoredRun]:
        return self._list("", ())

    def runs_of(self, workflow: str) -> list[StoredRun]:
        return self._list("WHERE workflow = ?", (workflow,))

    def runs_of_origin(self, origin: str | None) -> list[StoredRun]:
        """The runs of one origin: a deployment id, or None for the runs of this process."""
        if origin is None:
            return self._list("WHERE origin IS NULL", ())
        return self._list("WHERE origin = ?", (origin,))

    def count_by_origin(self) -> dict[str, int]:
        """How many runs each deployment has shipped (runs of this process are not counted)."""
        rows = self.db.execute(
            "SELECT origin, COUNT(*) FROM runs WHERE origin IS NOT NULL GROUP BY origin"
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def last_seq(self, run_id: str) -> int:
        """The number of the last event of a run, or -1 when it has none."""
        row = self.db.execute(
            "SELECT COALESCE(MAX(seq), -1) FROM run_events WHERE run_id = ?", (run_id,)
        ).fetchone()
        return int(row[0])

    def _list(self, where: str, args: tuple[object, ...]) -> list[StoredRun]:
        rows = self.db.execute(
            f"SELECT {_RUN_COLUMNS} FROM runs {where} ORDER BY created_at, run_id", args
        ).fetchall()
        return [_stored(r) for r in rows]

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
        batch = list(events)
        self._atomically(lambda: self._store(run_id, batch))

    def ingest(
        self,
        run_id: str,
        workflow: str,
        version: int,
        inputs: dict[str, object],
        created_at: str,
        origin: str,
        events: Iterable[Event],
    ) -> int:
        """Store a batch shipped by a service: the run (made if it is new) and its events, in one
        transaction, so a refused batch leaves no run behind. Returns the number of the last event
        the run has. The rules of ``append`` and ``ensure`` apply."""
        batch = list(events)

        def work() -> int:
            self.ensure(run_id, workflow, version, inputs, created_at, origin)
            return self._store(run_id, batch)

        return self._atomically(work)

    def _atomically(self, work: Callable[[], T]) -> T:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            done = work()
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")
        return done

    def _store(self, run_id: str, events: list[Event]) -> int:
        last = self.last_seq(run_id)
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
                    f"{run_id}: expected event {last + 1}, got {event.seq}", last + 1
                )
            self.db.execute(
                f"INSERT INTO run_events (run_id, {_EVENT_COLUMNS}) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                _values(run_id, event),
            )
            last = event.seq
        return last
