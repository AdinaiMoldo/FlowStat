"""The position of a service in its own history: how far the core has acknowledged (US-31)."""

from __future__ import annotations

import sqlite3

from flowstat.model.events import Event, EventKind
from flowstat.repo.db import open_database
from flowstat.repo.outbox import OutboxStore
from flowstat.repo.runs import RunStore

T = "2026-11-09T10:00:00Z"


def setup() -> tuple[sqlite3.Connection, RunStore, OutboxStore]:
    db = open_database(":memory:")
    return db, RunStore(db), OutboxStore(db)


def history(n: int) -> list[Event]:
    return [Event(i, EventKind.STEP_STARTED, float(i), step="a") for i in range(n)]


def test_a_run_nothing_was_sent_for_starts_before_its_first_event() -> None:
    _, runs, outbox = setup()
    runs.create("r1", "demo", 1, {}, T)
    assert outbox.shipped_through("r1") == -1


def test_the_position_moves_forward_and_never_back_by_itself() -> None:
    _, runs, outbox = setup()
    runs.create("r1", "demo", 1, {}, T)
    outbox.set("r1", 4)
    assert outbox.shipped_through("r1") == 4
    outbox.set("r1", 9)
    assert outbox.shipped_through("r1") == 9
    outbox.set("r1", 2, allow_back=True)  # the core said it has less
    assert outbox.shipped_through("r1") == 2


def test_the_runs_with_events_after_the_position_are_pending_oldest_first() -> None:
    _, runs, outbox = setup()
    runs.create("r2", "demo", 1, {}, "2026-11-09T10:00:02Z")
    runs.create("r1", "demo", 1, {}, "2026-11-09T10:00:01Z")
    runs.create("r3", "demo", 1, {}, "2026-11-09T10:00:03Z")  # no events: nothing to send
    runs.append("r1", history(3))
    runs.append("r2", history(2))
    assert [r.run_id for r in outbox.pending()] == ["r1", "r2"]
    outbox.set("r1", 2)
    assert [r.run_id for r in outbox.pending()] == ["r2"]
    runs.append("r1", [Event(3, EventKind.RUN_FINISHED, 3.0)])  # the run goes on: pending again
    assert [r.run_id for r in outbox.pending()] == ["r1", "r2"]


def test_the_events_after_a_position_come_in_order_and_in_chunks() -> None:
    _, runs, outbox = setup()
    runs.create("r1", "demo", 1, {}, T)
    runs.append("r1", history(7))
    assert [e.seq for e in outbox.events_after("r1", -1, 3)] == [0, 1, 2]
    assert [e.seq for e in outbox.events_after("r1", 2, 3)] == [3, 4, 5]
    assert [e.seq for e in outbox.events_after("r1", 5, 3)] == [6]
    assert outbox.events_after("r1", 6, 3) == []


def test_a_run_that_is_leaving_or_has_left_is_not_shipped() -> None:
    from flowstat.repo.ownership import OwnershipStore

    db, runs, outbox = setup()
    owners = OwnershipStore(db)
    for run_id in ("r1", "r2", "r3"):
        runs.create(run_id, "demo", 1, {}, T)
        runs.append(run_id, history(2))
    owners.fence("r2", 0, 1, "svc-2", 2)  # leaving
    owners.fence("r3", 0, 1, "svc-2", 2)
    owners.release("r3", 1)  # gone
    assert [r.run_id for r in outbox.pending()] == ["r1"]
