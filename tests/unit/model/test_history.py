"""Tests of the run report: a fold over the events (US-11)."""

from __future__ import annotations

from flowstat.model.events import Event, EventKind
from flowstat.model.history import report_from
from flowstat.model.states import RunOutcome, StepState

S = StepState
STEPS = ["a", "b", "c"]


def fin(seq: int, at: float, step: str, state: StepState, **kw: object) -> Event:
    return Event(seq, EventKind.STEP_FINISHED, at, step=step, state=state, **kw)  # type: ignore[arg-type]


def started(seq: int, at: float, step: str) -> Event:
    return Event(seq, EventKind.STEP_STARTED, at, step=step)


def test_no_events_means_a_created_run_with_pending_steps() -> None:
    report = report_from("r1", "demo", 1, [], STEPS)
    assert report.status == "CREATED" and report.outcome is None
    assert [(s.step, s.state) for s in report.steps] == [(x, S.PENDING) for x in STEPS]
    assert report.started_at is None and report.finished_at is None


def test_each_step_shows_state_start_end_and_error() -> None:
    log = [
        Event(0, EventKind.RUN_STARTED, 10.0),
        started(1, 10.0, "a"),
        fin(2, 12.0, "a", S.SUCCEEDED, output={"n": 1}),
        started(3, 12.0, "b"),
        fin(4, 13.5, "b", S.FAILED, code="E-ACT-002", message="cannot read BIS.xlsx"),
        fin(5, 13.5, "c", S.UPSTREAM_FAILED),
        Event(6, EventKind.RUN_FINISHED, 13.5, outcome=RunOutcome.FAILED),
    ]
    report = report_from("r1", "demo", 1, log, STEPS)
    a, b, c = report.steps
    assert (a.state, a.started_at, a.finished_at, a.code, a.output) == (
        S.SUCCEEDED,
        10.0,
        12.0,
        None,
        {"n": 1},
    )
    assert (b.state, b.started_at, b.finished_at) == (S.FAILED, 12.0, 13.5)
    assert (b.code, b.message) == ("E-ACT-002", "cannot read BIS.xlsx")
    assert (c.state, c.started_at, c.finished_at) == (S.UPSTREAM_FAILED, None, 13.5)
    assert (report.status, report.outcome) == ("FINISHED", RunOutcome.FAILED)
    assert (report.started_at, report.finished_at) == (10.0, 13.5)


def test_steps_keep_the_order_of_the_plan() -> None:
    log = [Event(0, EventKind.RUN_STARTED, 0.0), started(1, 0.0, "c"), started(2, 0.0, "a")]
    assert [s.step for s in report_from("r", "d", 1, log, STEPS).steps] == STEPS


def test_a_running_run_shows_running_steps() -> None:
    log = [Event(0, EventKind.RUN_STARTED, 0.0), started(1, 0.0, "a")]
    report = report_from("r", "d", 1, log, STEPS)
    assert report.status == "RUNNING"
    assert [s.state for s in report.steps] == [S.RUNNING, S.PENDING, S.PENDING]


def test_a_run_whose_only_active_step_waits_is_waiting() -> None:
    log = [
        Event(0, EventKind.RUN_STARTED, 0.0),
        started(1, 0.0, "a"),
        Event(2, EventKind.STEP_WAITING, 1.0, step="a"),
    ]
    report = report_from("r", "d", 1, log, STEPS)
    assert report.status == "WAITING" and report.steps[0].state is S.WAITING


def test_a_waiting_step_keeps_its_start_and_gets_an_end_when_answered() -> None:
    log = [
        Event(0, EventKind.RUN_STARTED, 0.0),
        started(1, 2.0, "a"),
        Event(2, EventKind.STEP_WAITING, 3.0, step="a"),
        fin(3, 9.0, "a", S.SUCCEEDED, output={"decision": "approved"}),
    ]
    a = report_from("r", "d", 1, log, STEPS).steps[0]
    assert (a.started_at, a.finished_at, a.state) == (2.0, 9.0, S.SUCCEEDED)


def test_a_running_step_next_to_a_waiting_one_keeps_the_run_running() -> None:
    log = [
        Event(0, EventKind.RUN_STARTED, 0.0),
        started(1, 0.0, "a"),
        started(2, 0.0, "b"),
        Event(3, EventKind.STEP_WAITING, 1.0, step="a"),
    ]
    assert report_from("r", "d", 1, log, STEPS).status == "RUNNING"


def test_the_report_counts_the_attempts_of_a_step() -> None:

    def retry(seq: int, at: float, attempt: int) -> Event:
        return Event(
            seq, EventKind.STEP_RETRY, at, step="a", attempt=attempt, delay=1.0, code="E-ACT-008"
        )

    log = [
        Event(0, EventKind.RUN_STARTED, 0.0),
        started(1, 0.0, "a"),
        retry(2, 1.0, 2),
        retry(3, 3.0, 3),
        fin(4, 6.0, "a", S.SUCCEEDED, output={}),
    ]
    a, b, _ = report_from("r", "d", 1, log, STEPS).steps
    assert (a.attempts, a.state, a.started_at, a.finished_at) == (3, S.SUCCEEDED, 0.0, 6.0)
    assert b.attempts == 0  # never ran


def test_a_step_that_ran_once_has_one_attempt() -> None:
    log = [Event(0, EventKind.RUN_STARTED, 0.0), started(1, 0.0, "a")]
    assert report_from("r", "d", 1, log, STEPS).steps[0].attempts == 1


def compensation_log() -> list[Event]:
    return [
        Event(0, EventKind.RUN_STARTED, 0.0),
        started(1, 0.0, "a"),
        fin(2, 1.0, "a", S.SUCCEEDED, output={}),
        started(3, 1.0, "b"),
        fin(4, 2.0, "b", S.FAILED, code="E-ACT-002", message="b broke"),
        Event(5, EventKind.COMPENSATION_STARTED, 2.0),
        Event(6, EventKind.STEP_COMPENSATING, 2.0, step="a"),
    ]


def test_a_run_that_is_undoing_its_work_is_compensating() -> None:
    report = report_from("r", "d", 1, compensation_log(), ["a", "b"])
    assert report.status == "COMPENSATING" and report.outcome is None
    assert report.steps[0].state is S.COMPENSATING


def test_an_undone_step_keeps_the_times_of_its_work_and_gets_its_own() -> None:
    log = [
        *compensation_log(),
        fin(7, 5.0, "a", S.COMPENSATED),
        Event(8, EventKind.RUN_FINISHED, 5.0, outcome=RunOutcome.COMPENSATED),
    ]
    report = report_from("r", "d", 1, log, ["a", "b"])
    a = report.steps[0]
    assert (a.state, a.started_at, a.finished_at, a.compensated_at) == (
        S.COMPENSATED,
        0.0,
        1.0,
        5.0,
    )
    assert (report.status, report.outcome) == ("FINISHED", RunOutcome.COMPENSATED)


def test_a_failed_undo_shows_its_error() -> None:
    log = [
        *compensation_log(),
        fin(7, 5.0, "a", S.COMPENSATION_FAILED, code="E-COMP-001", message="undo lost"),
        Event(8, EventKind.RUN_FINISHED, 5.0, outcome=RunOutcome.COMPENSATION_FAILED),
    ]
    a = report_from("r", "d", 1, log, ["a", "b"]).steps[0]
    assert a.state is S.COMPENSATION_FAILED and (a.code, a.message) == ("E-COMP-001", "undo lost")
