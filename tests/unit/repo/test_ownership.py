"""Ownership of a run: the fence, the import and the release are single transactions (US-33)."""

from __future__ import annotations

import sqlite3

import pytest

from flowstat.model.events import Event, EventKind
from flowstat.model.ownership import Ownership, OwnerState
from flowstat.model.states import StepState
from flowstat.repo.db import open_database
from flowstat.repo.ownership import (
    EpochMismatchError,
    Journal,
    NotOwnerError,
    OwnershipStore,
    RunChangedError,
)
from flowstat.repo.runs import RunStore, StoredRun

T = "2026-11-09T10:00:00Z"
Own = OwnerState


def history() -> list[Event]:
    return [
        Event(0, EventKind.RUN_STARTED, 0.0),
        Event(1, EventKind.STEP_STARTED, 1.0, step="a"),
        Event(2, EventKind.STEP_FINISHED, 2.0, step="a", state=StepState.SUCCEEDED, output={}),
    ]


def made() -> tuple[RunStore, OwnershipStore]:
    db = open_database(":memory:")
    runs = RunStore(db)
    runs.create("r1", "demo", 1, {"n": 1}, T)
    runs.append("r1", history())
    return runs, OwnershipStore(db)


def moved(version: int = 2) -> StoredRun:
    return StoredRun("r1", "demo", version, {"n": 1, "m": 4}, T)


def test_a_run_without_a_row_is_owned_at_epoch_zero() -> None:
    _, owners = made()
    assert owners.get("r1") == Ownership("r1", 0, Own.OWNED)
    assert owners.get("r1").owned


def test_the_fence_takes_the_next_epoch_and_names_the_target() -> None:
    _, owners = made()
    fenced = owners.fence("r1", 0, 2, "svc-2", 2)
    assert fenced == Ownership("r1", 1, Own.MIGRATING, "svc-2", 2)
    assert owners.get("r1") == fenced and not fenced.owned


def test_the_same_fence_again_answers_the_same() -> None:
    _, owners = made()
    first = owners.fence("r1", 0, 2, "svc-2", 2)
    assert owners.fence("r1", 0, 2, "svc-2", 2) == first


def test_a_fence_for_another_target_is_refused() -> None:
    _, owners = made()
    owners.fence("r1", 0, 2, "svc-2", 2)
    with pytest.raises(NotOwnerError):
        owners.fence("r1", 0, 2, "svc-3", 2)


def test_a_history_that_grew_since_the_check_is_not_fenced() -> None:
    runs, owners = made()
    runs.append("r1", [Event(3, EventKind.STEP_STARTED, 3.0, step="b")])
    with pytest.raises(RunChangedError) as raised:
        owners.fence("r1", 0, 2, "core", 2)
    assert (raised.value.expected, raised.value.actual) == (2, 3)
    assert owners.get("r1").owned  # nothing was changed


def test_the_epoch_must_be_the_current_one() -> None:
    _, owners = made()
    with pytest.raises(EpochMismatchError):
        owners.fence("r1", 4, 2, "core", 2)


def test_release_turns_migrating_into_migrated_out_and_is_repeatable() -> None:
    _, owners = made()
    owners.fence("r1", 0, 2, "svc-2", 2)
    out = owners.release("r1", 1)
    assert out == Ownership("r1", 1, Own.MIGRATED_OUT, "svc-2", 2)
    assert owners.release("r1", 1) == out


def test_release_without_a_fence_is_refused() -> None:
    _, owners = made()
    with pytest.raises(NotOwnerError):
        owners.release("r1", 1)


def test_adopt_makes_the_run_stores_its_history_and_marks_the_move() -> None:
    target = RunStore(open_database(":memory:"))
    mine = target.adopt(moved(), history(), 1, "core", 5.0)
    assert mine == Ownership("r1", 1, Own.OWNED, "core")
    assert target.get("r1").version == 2 and target.get("r1").inputs == {"n": 1, "m": 4}
    last = target.events("r1")[-1]
    assert (last.seq, last.kind, last.attempt, last.message) == (
        3,
        EventKind.RUN_MIGRATED,
        1,
        "core",
    )
    assert [e.seq for e in target.events("r1")] == [0, 1, 2, 3]


def test_adopt_again_changes_nothing() -> None:
    target = RunStore(open_database(":memory:"))
    target.adopt(moved(), history(), 1, "core", 5.0)
    again = target.adopt(moved(), history(), 1, "core", 9.0)
    assert again.epoch == 1 and len(target.events("r1")) == 4


def test_adopt_updates_the_copy_the_core_already_has() -> None:
    db = open_database(":memory:")
    core = RunStore(db)
    core.create("r1", "demo", 1, {"n": 1}, T, origin="svc-1")
    core.append("r1", history()[:2])  # the copy lags by one event
    core.adopt(moved(), history(), 1, "svc-1", 5.0)
    kept = core.get("r1")
    assert (kept.version, kept.origin) == (2, None)
    assert [e.kind for e in core.events("r1")][-2:] == [
        EventKind.STEP_FINISHED,
        EventKind.RUN_MIGRATED,
    ]


def test_adopt_at_a_lower_epoch_than_the_one_held_is_refused() -> None:
    db = open_database(":memory:")
    runs, owners = RunStore(db), OwnershipStore(db)
    runs.adopt(moved(), history(), 2, "core", 5.0)
    other = StoredRun("r1", "demo", 2, {}, T)
    assert runs.adopt(other, history(), 2, "core", 6.0).epoch == 2  # the same epoch: a repeat
    owners.fence("r1", 2, 3, "svc-9", 3)
    with pytest.raises(NotOwnerError):
        runs.adopt(other, history(), 3, "core", 7.0)  # fenced at 3: not ours to take again


def test_adopt_refuses_a_history_that_differs_from_the_copy() -> None:
    db = open_database(":memory:")
    runs = RunStore(db)
    runs.create("r1", "demo", 1, {}, T, origin="svc-1")
    runs.append("r1", [Event(0, EventKind.RUN_STARTED, 7.0)])
    from flowstat.repo.runs import EventConflictError

    with pytest.raises(EventConflictError):
        runs.adopt(moved(), history(), 1, "svc-1", 5.0)
    assert runs.get("r1").origin == "svc-1"  # the whole transaction was rolled back
    assert OwnershipStore(db).get("r1").epoch == 0


def test_retarget_moves_a_run_in_the_same_store() -> None:
    runs, owners = made()
    done = runs.retarget("r1", 0, 2, 2, {"n": 1, "m": 4}, 5.0)
    assert done.epoch == 1 and done.owned
    assert runs.get("r1").version == 2 and runs.events("r1")[-1].kind is EventKind.RUN_MIGRATED
    assert runs.retarget("r1", 0, 2, 2, {"n": 1, "m": 4}, 6.0) == done  # a repeat
    assert len(runs.events("r1")) == 4
    assert owners.get("r1") == done


def test_retarget_is_refused_when_the_history_grew() -> None:
    runs, _ = made()
    runs.append("r1", [Event(3, EventKind.STEP_STARTED, 3.0, step="b")])
    with pytest.raises(RunChangedError):
        runs.retarget("r1", 0, 2, 2, {}, 5.0)
    assert runs.get("r1").version == 1


def test_the_journal_keeps_the_handovers_in_flight() -> None:
    journal = Journal(open_database(":memory:"))
    journal.begin("r1", '{"x": 1}')
    assert journal.pending() == [("r1", '{"x": 1}')]
    with pytest.raises(sqlite3.IntegrityError):
        journal.begin("r1", "{}")
    journal.end("r1")
    assert journal.pending() == []
