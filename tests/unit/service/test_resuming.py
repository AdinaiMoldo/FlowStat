"""Which runs a process that starts has to take up (US-36)."""

from __future__ import annotations

import sqlite3

from flowstat.model.events import Event, EventKind
from flowstat.model.states import RunOutcome, StepState
from flowstat.repo.db import open_database
from flowstat.repo.ownership import OwnershipStore
from flowstat.repo.runs import RunStore
from flowstat.service.resuming import interrupted
from flowstat.service.runs import RunService
from flowstat.service.workflows import WorkflowService

K = EventKind
T = "2026-11-11T10:00:00Z"


def setup() -> tuple[sqlite3.Connection, RunStore, RunService]:
    db = open_database(":memory:")
    return db, RunStore(db), RunService(db, WorkflowService(db))


def add(store: RunStore, run_id: str, events: list[Event], origin: str | None = None) -> None:
    store.create(run_id, "demo", 1, {}, T, origin=origin)
    if events:
        store.append(run_id, events)


STARTED = Event(0, K.RUN_STARTED, 0.0)
RUNNING = [STARTED, Event(1, K.STEP_STARTED, 1.0, step="a")]
WAITING = [*RUNNING, Event(2, K.STEP_WAITING, 2.0, step="a")]
FINISHED = [STARTED, Event(1, K.RUN_FINISHED, 1.0, outcome=RunOutcome.SUCCEEDED)]


def ids(runs: RunService) -> list[str]:
    return [r.run_id for r in interrupted(runs)]


def test_a_run_that_was_stored_and_never_started_is_to_be_taken_up() -> None:
    _, store, runs = setup()
    add(store, "r1", [])
    assert ids(runs) == ["r1"]


def test_a_run_with_a_step_in_flight_is_to_be_taken_up() -> None:
    _, store, runs = setup()
    add(store, "r1", RUNNING)
    assert ids(runs) == ["r1"]


def test_a_run_that_is_waiting_for_a_person_or_is_finished_is_left_alone() -> None:
    _, store, runs = setup()
    add(store, "waiting", WAITING)
    add(store, "finished", FINISHED)
    assert ids(runs) == []


def test_a_run_that_is_undoing_its_work_is_to_be_taken_up() -> None:
    _, store, runs = setup()
    history = [
        *RUNNING,
        Event(2, K.STEP_FINISHED, 2.0, step="a", state=StepState.SUCCEEDED),
        Event(3, K.COMPENSATION_STARTED, 3.0),
        Event(4, K.STEP_STARTED, 4.0, step="a", state=StepState.COMPENSATING),
    ]
    add(store, "r1", history)
    assert ids(runs) == ["r1"]


def test_the_runs_come_oldest_first() -> None:
    _, store, runs = setup()
    store.create("b", "demo", 1, {}, "2026-11-11T10:00:02Z")
    store.create("a", "demo", 1, {}, "2026-11-11T10:00:01Z")
    assert ids(runs) == ["a", "b"]


def test_a_run_that_is_not_ours_is_not_taken_up() -> None:
    db, store, runs = setup()
    owners = OwnershipStore(db)
    add(store, "leaving", RUNNING)
    add(store, "left", RUNNING)
    add(store, "copy", RUNNING, origin="svc-1")  # the core's copy of a run a service drives
    add(store, "mine", RUNNING)
    owners.fence("leaving", 0, 1, "svc-2", 2)
    owners.fence("left", 0, 1, "svc-2", 2)
    owners.release("left", 1)
    assert ids(runs) == ["mine"]
