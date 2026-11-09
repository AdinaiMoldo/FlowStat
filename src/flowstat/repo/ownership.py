"""A2: who owns a run, and the journal of the handovers the core has begun (US-33).

Ownership is a table, not an event: the history of a run keeps its key (run, sequence number) and
its meaning when the same events are copied to the core, while the fence has to be taken and
released in one transaction with a check of the state it replaces.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from flowstat.model.ownership import Ownership, OwnerState


class NotOwnerError(RuntimeError):
    """The store does not own the run (any more): it is fenced or the run went elsewhere."""

    def __init__(self, ownership: Ownership) -> None:
        super().__init__(
            f"{ownership.run_id} is {ownership.state.value} at epoch {ownership.epoch}"
        )
        self.ownership = ownership


class EpochMismatchError(RuntimeError):
    """The caller acted on an epoch that is not the current one."""


class RunChangedError(RuntimeError):
    """The history grew after the check: ask again."""

    def __init__(self, run_id: str, expected: int, actual: int) -> None:
        super().__init__(
            f"{run_id}: the history has {actual} as last event, the check saw {expected}"
        )
        self.expected = expected
        self.actual = actual


def read(db: sqlite3.Connection, run_id: str) -> Ownership:
    row = db.execute(
        "SELECT epoch, state, peer, version FROM ownership WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None:
        return Ownership(run_id)
    return Ownership(run_id, int(row[0]), OwnerState(row[1]), row[2], row[3])


def write(db: sqlite3.Connection, o: Ownership) -> None:
    db.execute(
        "INSERT INTO ownership (run_id, epoch, state, peer, version) VALUES (?,?,?,?,?) "
        "ON CONFLICT(run_id) DO UPDATE SET epoch = excluded.epoch, state = excluded.state, "
        "peer = excluded.peer, version = excluded.version",
        (o.run_id, o.epoch, o.state.value, o.peer, o.version),
    )


class OwnershipStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    def get(self, run_id: str) -> Ownership:
        return read(self.db, run_id)

    def all(self) -> list[Ownership]:
        rows = self.db.execute(
            "SELECT run_id, epoch, state, peer, version FROM ownership ORDER BY run_id"
        ).fetchall()
        return [Ownership(r[0], int(r[1]), OwnerState(r[2]), r[3], r[4]) for r in rows]

    def fence(self, run_id: str, epoch: int, last_seq: int, peer: str, version: int) -> Ownership:
        """OWNED(epoch) becomes MIGRATING(epoch + 1) if nothing was added to the history after
        event ``last_seq``. The same call again, after it succeeded, answers the same."""
        return self._atomically(lambda: self._fence(run_id, epoch, last_seq, peer, version))

    def _fence(self, run_id: str, epoch: int, last_seq: int, peer: str, version: int) -> Ownership:
        current = read(self.db, run_id)
        fenced = Ownership(run_id, epoch + 1, OwnerState.MIGRATING, peer, version)
        if current == fenced:
            return current
        if not current.owned:
            raise NotOwnerError(current)
        if current.epoch != epoch:
            raise EpochMismatchError(f"{run_id}: the epoch is {current.epoch}, not {epoch}")
        row = self.db.execute(
            "SELECT COALESCE(MAX(seq), -1) FROM run_events WHERE run_id = ?", (run_id,)
        ).fetchone()
        if int(row[0]) != last_seq:
            raise RunChangedError(run_id, last_seq, int(row[0]))
        write(self.db, fenced)
        return fenced

    def release(self, run_id: str, epoch: int) -> Ownership:
        """MIGRATING(epoch) becomes MIGRATED_OUT(epoch); the same call again answers the same."""
        return self._atomically(lambda: self._release(run_id, epoch))

    def _release(self, run_id: str, epoch: int) -> Ownership:
        current = read(self.db, run_id)
        if current.epoch == epoch and current.state is OwnerState.MIGRATED_OUT:
            return current
        if current.epoch != epoch or current.state is not OwnerState.MIGRATING:
            raise NotOwnerError(current)
        released = Ownership(run_id, epoch, OwnerState.MIGRATED_OUT, current.peer, current.version)
        write(self.db, released)
        return released

    def _atomically(self, work: Callable[[], Ownership]) -> Ownership:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            done = work()
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")
        return done


class Journal:
    """The handovers the core has begun and not finished: what a restart takes forward."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    def begin(self, run_id: str, plan: str) -> None:
        """Record the plan of a handover. A run has at most one in flight (sqlite3.IntegrityError
        otherwise)."""
        self.db.execute("INSERT INTO handovers (run_id, plan) VALUES (?, ?)", (run_id, plan))

    def pending(self) -> list[tuple[str, str]]:
        rows = self.db.execute("SELECT run_id, plan FROM handovers ORDER BY run_id").fetchall()
        return [(r[0], r[1]) for r in rows]

    def end(self, run_id: str) -> None:
        self.db.execute("DELETE FROM handovers WHERE run_id = ?", (run_id,))
