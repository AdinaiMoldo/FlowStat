"""Tests of the write-ahead log (US-15): events are stored before their commands run."""

from __future__ import annotations

import pytest

from flowstat.engine.scheduler import Scheduler
from flowstat.model.events import Event, EventKind
from flowstat.recovery.wal import WriteAheadLog
from flowstat.repo.db import open_database
from flowstat.repo.runs import RunStore
from tests.unit.engine.helpers import doc, plan_of

T = "2026-11-05T10:00:00Z"


def new_log() -> tuple[RunStore, WriteAheadLog]:
    store = RunStore(open_database(":memory:"))
    store.create("r1", "demo", 1, {}, T)
    return store, WriteAheadLog(store, "r1")


def scheduler() -> Scheduler:
    steps = "  a:\n    action: x.y\n  b:\n    action: x.y\n    needs: [a]\n"
    return Scheduler(plan_of(doc(steps, "goals: {any_of: [b]}\n")), {}, 1)


def test_commit_stores_the_events_that_are_not_stored_yet() -> None:
    store, wal = new_log()
    s = scheduler()
    s.start(0.0)

    wal.commit(s.events)
    s.step_done("a", {}, 1.0)
    wal.commit(s.events)

    assert store.events("r1") == s.events


def test_committing_twice_stores_nothing_twice() -> None:
    store, wal = new_log()
    s = scheduler()
    s.start(0.0)

    wal.commit(s.events)
    wal.commit(s.events)

    assert len(store.events("r1")) == len(s.events)


def test_a_log_that_continues_a_stored_history_starts_after_it() -> None:
    store, _ = new_log()
    s = scheduler()
    s.start(0.0)
    store.append("r1", s.events)

    wal = WriteAheadLog(store, "r1", stored=len(s.events))
    s.step_done("a", {}, 1.0)
    wal.commit(s.events)

    assert store.events("r1") == s.events


def test_a_commit_is_one_transaction() -> None:
    store, wal = new_log()
    bad = [Event(0, EventKind.RUN_STARTED, 0.0), Event(5, EventKind.RUN_STARTED, 0.0)]

    with pytest.raises(Exception, match="expected event 1"):
        wal.commit(bad)

    assert store.events("r1") == []


def test_the_database_is_set_to_full_synchronisation(tmp_path: object) -> None:
    db = open_database(":memory:")
    assert db.execute("PRAGMA synchronous").fetchone()[0] in (2, 3)  # FULL (2) or EXTRA (3)
