"""The owners of a run for the handover (US-33): the store of this process, and the journal and
the mirror the core keeps. The owner behind HTTP is in ``service/remote.py``.
"""

from __future__ import annotations

import sqlite3
from typing import Protocol

from flowstat.migration.handover import Plan, Snapshot
from flowstat.model.ownership import Ownership
from flowstat.repo.ownership import Journal as JournalTable
from flowstat.repo.ownership import OwnershipStore
from flowstat.repo.runs import RunStore, StoredRun


class RunBusyError(RuntimeError):
    """A thread is driving the run: it cannot be fenced now."""


class Guard(Protocol):
    """The lock of a run (``api.locks.RunLocks`` is one)."""

    def try_acquire(self, run_id: str) -> bool: ...

    def release(self, run_id: str) -> None: ...


class LocalOwner:
    """The runs of one database. ``guard`` is the lock a thread holds while it drives a run."""

    def __init__(self, db: sqlite3.Connection, guard: Guard) -> None:
        self.runs = RunStore(db)
        self.owners = OwnershipStore(db)
        self.guard = guard

    def ownership(self, run_id: str) -> Ownership:
        return self.owners.get(run_id)

    def export(self, run_id: str) -> Snapshot:
        run = self.runs.get(run_id)
        return Snapshot(
            run_id,
            run.workflow,
            run.version,
            run.inputs,
            run.created_at,
            tuple(self.runs.events(run_id)),
            self.owners.get(run_id),
        )

    def fence(self, plan: Plan) -> Ownership:
        if not self.guard.try_acquire(plan.run_id):
            raise RunBusyError(f"the run {plan.run_id} is being driven right now")
        try:
            return self.owners.fence(
                plan.run_id, plan.epoch, plan.last_seq, plan.target, plan.version
            )
        finally:
            self.guard.release(plan.run_id)

    def adopt(self, snapshot: Snapshot, plan: Plan) -> Ownership:
        run = StoredRun(
            plan.run_id, snapshot.workflow, plan.version, dict(plan.inputs), snapshot.created_at
        )
        return self.runs.adopt(run, snapshot.events, plan.epoch + 1, plan.source, plan.at)

    def release(self, plan: Plan) -> Ownership:
        return self.owners.release(plan.run_id, plan.epoch + 1)

    def retarget(self, plan: Plan) -> Ownership:
        if not self.guard.try_acquire(plan.run_id):
            raise RunBusyError(f"the run {plan.run_id} is being driven right now")
        try:
            return self.runs.retarget(
                plan.run_id, plan.epoch, plan.last_seq, plan.version, dict(plan.inputs), plan.at
            )
        finally:
            self.guard.release(plan.run_id)


class CoreJournal:
    """The handovers the core has begun, kept in its database."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self.table = JournalTable(db)

    def begin(self, plan: Plan) -> None:
        self.table.begin(plan.run_id, plan.to_json())

    def pending(self) -> list[Plan]:
        return [Plan.from_json(text) for _, text in self.table.pending()]

    def end(self, run_id: str) -> None:
        self.table.end(run_id)


class CoreMirror:
    """The core's copy of a run says where the run is (origin None: the core)."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self.runs = RunStore(db)

    def moved(self, plan: Plan) -> None:
        try:
            self.runs.get(plan.run_id)
        except LookupError:
            return  # the core never saw this run
        origin = None if plan.target == "core" else plan.target
        self.runs.set_location(plan.run_id, origin, plan.version, dict(plan.inputs))
