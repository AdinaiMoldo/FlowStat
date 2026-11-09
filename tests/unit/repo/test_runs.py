"""Tests of the run store (A2): the history of a run is append-only and survives a restart."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from flowstat.model.events import Event, EventKind
from flowstat.model.states import RunOutcome, StepState
from flowstat.repo.db import open_database
from flowstat.repo.runs import EventConflictError, RunNotFoundError, RunStore

T = "2026-11-05T10:00:00Z"


def store() -> RunStore:
    return RunStore(open_database(":memory:"))


def events() -> list[Event]:
    return [
        Event(0, EventKind.RUN_STARTED, 0.0),
        Event(1, EventKind.STEP_STARTED, 0.0, step="a"),
        Event(
            2,
            EventKind.STEP_FINISHED,
            1.5,
            step="a",
            state=StepState.SUCCEEDED,
            output={"rows": [1, 2], "ok": True},
        ),
        Event(3, EventKind.RUN_FINISHED, 1.5, outcome=RunOutcome.SUCCEEDED),
    ]


def test_a_created_run_is_found_with_its_inputs() -> None:
    s = store()
    s.create("r1", "demo", 2, {"quarter": "2025Q1"}, T)
    run = s.get("r1")
    assert (run.run_id, run.workflow, run.version, run.inputs) == (
        "r1",
        "demo",
        2,
        {"quarter": "2025Q1"},
    )
    assert run.created_at == T


def test_a_run_id_is_used_once() -> None:
    s = store()
    s.create("r1", "demo", 1, {}, T)
    with pytest.raises(sqlite3.IntegrityError):
        s.create("r1", "demo", 1, {}, T)


def test_an_unknown_run_is_reported() -> None:
    with pytest.raises(RunNotFoundError):
        store().get("nope")
    with pytest.raises(RunNotFoundError):
        store().events("nope")


def test_events_come_back_equal_and_in_order() -> None:
    s = store()
    s.create("r1", "demo", 1, {}, T)
    s.append("r1", events())
    assert s.events("r1") == events()


def test_events_can_be_appended_in_pieces() -> None:
    s = store()
    s.create("r1", "demo", 1, {}, T)
    s.append("r1", events()[:2])
    s.append("r1", events()[2:])
    assert s.events("r1") == events()


def test_delivering_the_same_event_twice_stores_it_once() -> None:
    s = store()
    s.create("r1", "demo", 1, {}, T)
    s.append("r1", events())
    s.append("r1", events()[1:3])  # at-least-once delivery
    assert s.events("r1") == events()


def test_a_different_event_with_a_used_sequence_number_is_refused() -> None:
    s = store()
    s.create("r1", "demo", 1, {}, T)
    s.append("r1", events())
    with pytest.raises(EventConflictError):
        s.append("r1", [Event(1, EventKind.STEP_STARTED, 0.0, step="b")])


def test_a_gap_in_sequence_numbers_is_refused_and_nothing_is_stored() -> None:
    s = store()
    s.create("r1", "demo", 1, {}, T)
    with pytest.raises(EventConflictError):
        s.append("r1", [events()[0], events()[2]])
    assert s.events("r1") == []


def test_events_of_an_unknown_run_are_refused() -> None:
    with pytest.raises(RunNotFoundError):
        store().append("nope", events())


def test_stored_events_cannot_be_changed_or_deleted() -> None:
    s = store()
    s.create("r1", "demo", 1, {}, T)
    s.append("r1", events())
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        s.db.execute("UPDATE run_events SET at = 9 WHERE run_id = 'r1'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        s.db.execute("DELETE FROM run_events WHERE run_id = 'r1'")


def test_runs_of_a_workflow_are_listed_in_creation_order() -> None:
    s = store()
    s.create("r2", "demo", 1, {}, "2026-11-05T11:00:00Z")
    s.create("r1", "demo", 1, {}, T)
    s.create("r3", "other", 1, {}, T)
    assert [r.run_id for r in s.runs_of("demo")] == ["r1", "r2"]
    assert [r.run_id for r in s.runs()] == ["r1", "r3", "r2"]


def test_the_history_survives_closing_and_reopening_the_database(tmp_path: Path) -> None:
    path = str(tmp_path / "flowstat.db")
    db = open_database(path)
    first = RunStore(db)
    first.create("r1", "demo", 1, {"q": 1}, T)
    first.append("r1", events())
    db.close()
    again = RunStore(open_database(path))
    assert again.events("r1") == events()
    assert again.get("r1").inputs == {"q": 1}


def test_retry_events_keep_their_attempt_and_delay() -> None:
    s = store()
    s.create("r1", "demo", 1, {}, T)
    log = [
        Event(0, EventKind.RUN_STARTED, 0.0),
        Event(1, EventKind.STEP_STARTED, 0.0, step="a"),
        Event(
            2,
            EventKind.STEP_RETRY,
            1.0,
            step="a",
            attempt=2,
            delay=1.5,
            code="E-ACT-008",
            message="lost",
        ),
    ]
    s.append("r1", log)
    assert s.events("r1") == log


def test_a_database_made_before_retries_is_upgraded(tmp_path: Path) -> None:
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, workflow TEXT NOT NULL,"
        " version INTEGER NOT NULL, inputs TEXT NOT NULL, created_at TEXT NOT NULL);"
        "CREATE TABLE run_events (run_id TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL,"
        " at REAL NOT NULL, step TEXT, state TEXT, outcome TEXT, code TEXT, message TEXT,"
        " output TEXT, PRIMARY KEY (run_id, seq));"
        "INSERT INTO runs VALUES ('r1', 'demo', 1, '{}', 'T');"
        "INSERT INTO run_events (run_id, seq, kind, at) VALUES ('r1', 0, 'RUN_STARTED', 0.0);"
    )
    old.close()
    upgraded = RunStore(open_database(path))
    assert upgraded.events("r1") == [Event(0, EventKind.RUN_STARTED, 0.0)]
    upgraded.append("r1", [Event(1, EventKind.STEP_RETRY, 1.0, step="a", attempt=2, delay=1.0)])
    assert upgraded.events("r1")[1].attempt == 2


def test_the_events_of_a_recovery_come_back_equal() -> None:
    s = store()
    s.create("r1", "demo", 1, {}, T)
    log = [
        Event(0, EventKind.RUN_STARTED, 0.0),
        Event(1, EventKind.RUN_RESUMED, 5.0),
        Event(
            2,
            EventKind.STEP_UNCERTAIN,
            5.0,
            step="a",
            state=StepState.RUNNING,
            code="E-REC-001",
            message="unknown",
            attempt=1,
        ),
        Event(3, EventKind.STEP_RESOLVED, 6.0, step="a", decision="retry", attempt=2),
    ]
    s.append("r1", log)
    assert s.events("r1") == log


def test_a_database_made_before_recovery_is_upgraded(tmp_path: Path) -> None:
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, workflow TEXT NOT NULL,"
        " version INTEGER NOT NULL, inputs TEXT NOT NULL, created_at TEXT NOT NULL);"
        "CREATE TABLE run_events (run_id TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL,"
        " at REAL NOT NULL, step TEXT, state TEXT, outcome TEXT, code TEXT, message TEXT,"
        " output TEXT, attempt INTEGER, delay REAL, PRIMARY KEY (run_id, seq));"
        "INSERT INTO runs VALUES ('r1', 'demo', 1, '{}', 'T');"
        "INSERT INTO run_events (run_id, seq, kind, at) VALUES ('r1', 0, 'RUN_STARTED', 0.0);"
    )
    old.close()
    upgraded = RunStore(open_database(path))
    upgraded.append("r1", [Event(1, EventKind.STEP_RESOLVED, 1.0, step="a", decision="done")])
    assert upgraded.events("r1")[1].decision == "done"


# -- the origin of a run (US-31) ----------------------------------------------------------------
def test_a_run_of_the_core_has_no_origin_and_a_shipped_one_names_its_deployment() -> None:
    from flowstat.repo.runs import RunConflictError  # noqa: F401  (named here: part of the API)

    s = store()
    s.create("r1", "demo", 2, {}, T)
    s.create("r2", "demo", 2, {}, T, origin="d-1")
    assert s.get("r1").origin is None
    assert s.get("r2").origin == "d-1"
    assert [r.run_id for r in s.runs_of_origin("d-1")] == ["r2"]
    assert [r.run_id for r in s.runs_of_origin(None)] == ["r1"]
    assert [r.run_id for r in s.runs()] == ["r1", "r2"]


def test_the_runs_of_each_origin_are_counted() -> None:
    s = store()
    s.create("r1", "demo", 2, {}, T)
    s.create("r2", "demo", 2, {}, T, origin="d-1")
    s.create("r3", "demo", 2, {}, T, origin="d-1")
    assert s.count_by_origin() == {"d-1": 2}


def test_ensure_creates_a_run_once_and_a_repeat_changes_nothing() -> None:
    s = store()
    assert s.ensure("r1", "demo", 2, {"q": 1}, T, "d-1") is True
    assert s.ensure("r1", "demo", 2, {"q": 1}, "2030-01-01T00:00:00Z", "d-1") is False
    assert s.get("r1").created_at == T


@pytest.mark.parametrize(
    ("workflow", "version", "inputs", "origin"),
    [
        ("other", 2, {"q": 1}, "d-1"),
        ("demo", 3, {"q": 1}, "d-1"),
        ("demo", 2, {"q": 2}, "d-1"),
        ("demo", 2, {"q": 1}, "d-2"),
        ("demo", 2, {"q": 1}, None),
    ],
)
def test_ensure_refuses_a_run_that_is_not_the_same_one(
    workflow: str, version: int, inputs: dict[str, object], origin: str | None
) -> None:
    from flowstat.repo.runs import RunConflictError

    s = store()
    s.ensure("r1", "demo", 2, {"q": 1}, T, "d-1")
    with pytest.raises(RunConflictError):
        s.ensure("r1", workflow, version, inputs, T, origin)


def test_a_run_of_the_core_is_not_taken_over_by_a_shipped_one() -> None:
    from flowstat.repo.runs import RunConflictError

    s = store()
    s.create("r1", "demo", 2, {}, T)
    with pytest.raises(RunConflictError):
        s.ensure("r1", "demo", 2, {}, T, "d-1")


def test_the_last_event_number_of_a_run_is_told() -> None:
    s = store()
    s.create("r1", "demo", 2, {}, T)
    assert s.last_seq("r1") == -1
    s.append("r1", events())
    assert s.last_seq("r1") == 3


def test_a_database_of_an_earlier_version_gets_the_origin_column(tmp_path: Path) -> None:
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, workflow TEXT NOT NULL, version INTEGER "
        "NOT NULL, inputs TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    old.execute("INSERT INTO runs VALUES ('r0', 'demo', 1, '{}', 'x')")
    old.commit()
    old.close()
    s = RunStore(open_database(path))
    assert s.get("r0").origin is None
    s.create("r1", "demo", 1, {}, T, origin="d-9")
    assert s.get("r1").origin == "d-9"


def test_ingest_makes_the_run_and_stores_its_events_and_a_repeat_changes_nothing() -> None:
    s = store()
    last = s.ingest("r1", "demo", 2, {"q": 1}, T, "d-1", events())
    assert last == 3
    assert s.get("r1").origin == "d-1" and [e.seq for e in s.events("r1")] == [0, 1, 2, 3]
    assert s.ingest("r1", "demo", 2, {"q": 1}, T, "d-1", events()) == 3
    assert len(s.events("r1")) == 4
    assert s.ingest("r1", "demo", 2, {"q": 1}, T, "d-1", events()[:2]) == 3  # an old prefix


def test_a_refused_batch_leaves_no_run_behind() -> None:
    s = store()
    with pytest.raises(EventConflictError) as gap:
        s.ingest("r1", "demo", 2, {}, T, "d-1", events()[2:])  # starts at 2, not 0
    assert gap.value.expected == 0
    assert s.runs() == []
    with pytest.raises(EventConflictError):
        s.ingest("r2", "demo", 2, {}, T, "d-1", [*events()[:2], events()[3]])
    assert s.runs() == []


def test_a_batch_for_a_run_that_is_not_the_same_one_is_refused_and_changes_nothing() -> None:
    from flowstat.repo.runs import RunConflictError

    s = store()
    s.create("r1", "demo", 2, {}, T)
    s.append("r1", events()[:2])
    with pytest.raises(RunConflictError):
        s.ingest("r1", "demo", 2, {}, T, "d-1", events())
    assert len(s.events("r1")) == 2
