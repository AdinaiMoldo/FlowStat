"""Counting the runs of a workflow: failures, results that are not failures, durations (US-34)."""

from __future__ import annotations

from flowstat.model.events import Event, EventKind
from flowstat.model.states import RunOutcome
from flowstat.repo.db import open_database
from flowstat.repo.runs import RunStore, StoredRun
from flowstat.service.statistics import StatisticsService, summarise

T = "2026-11-09T10:00:00Z"
O = RunOutcome  # noqa: E741


def run(run_id: str, workflow: str = "demo", version: int = 1) -> StoredRun:
    return StoredRun(run_id, workflow, version, {}, T)


def ended(outcome: RunOutcome | None, start: float = 0.0, end: float = 10.0) -> list[Event]:
    events = [Event(0, EventKind.RUN_STARTED, start)]
    if outcome is not None:
        events.append(Event(1, EventKind.RUN_FINISHED, end, outcome=outcome))
    return events


def test_ten_runs_with_two_failures_have_a_failure_rate_of_one_fifth() -> None:
    rows = [(run(f"r{i}"), ended(O.SUCCEEDED)) for i in range(8)]
    rows += [(run("f1"), ended(O.FAILED)), (run("f2"), ended(O.COMPENSATED))]
    [stats] = summarise(rows)
    assert (stats.runs, stats.finished, stats.succeeded, stats.failed) == (10, 10, 8, 2)
    assert stats.failure_rate == 0.2


def test_every_outcome_that_stops_a_run_on_a_failure_counts_as_one() -> None:
    rows = [(run(o.value), ended(o)) for o in (O.FAILED, O.COMPENSATED, O.COMPENSATION_FAILED)]
    [stats] = summarise(rows)
    assert stats.failed == 3 and stats.failure_rate == 1.0


def test_a_goal_that_is_not_reached_is_a_result_and_not_a_failure() -> None:
    rows = [(run("a"), ended(O.GOAL_NOT_REACHED)), (run("b"), ended(O.SUCCEEDED))]
    [stats] = summarise(rows)
    assert (stats.not_reached, stats.succeeded, stats.failed) == (1, 1, 0)
    assert stats.failure_rate == 0.0


def test_a_cancelled_run_is_finished_and_not_a_failure() -> None:
    [stats] = summarise([(run("a"), ended(O.CANCELLED)), (run("b"), ended(O.FAILED))])
    assert (stats.finished, stats.cancelled, stats.failed) == (2, 1, 1)
    assert stats.failure_rate == 0.5


def test_runs_that_have_not_finished_are_running_and_stay_out_of_the_rate() -> None:
    rows = [(run("a"), ended(None)), (run("b"), []), (run("c"), ended(O.FAILED))]
    [stats] = summarise(rows)
    assert (stats.runs, stats.running, stats.finished) == (3, 2, 1)
    assert stats.failure_rate == 1.0


def test_nothing_finished_gives_no_rate_and_no_duration() -> None:
    [stats] = summarise([(run("a"), ended(None))])
    assert stats.failure_rate is None and stats.average_duration is None


def test_the_average_duration_is_over_finished_runs_of_the_clock_of_the_engine() -> None:
    rows = [
        (run("a"), ended(O.SUCCEEDED, 0.0, 10.0)),
        (run("b"), ended(O.FAILED, 100.0, 130.0)),
        (run("c"), ended(None, 5.0)),  # not finished: not in the mean
    ]
    [stats] = summarise(rows)
    assert stats.average_duration == 20.0


def test_a_finished_run_with_no_start_event_has_no_duration_but_is_counted() -> None:
    history = [Event(0, EventKind.RUN_FINISHED, 9.0, outcome=O.SUCCEEDED)]
    [stats] = summarise([(run("a"), history)])
    assert stats.succeeded == 1 and stats.average_duration is None


def test_the_versions_are_counted_apart_and_the_workflows_come_in_name_order() -> None:
    rows = [
        (run("a", "zeta", 1), ended(O.SUCCEEDED)),
        (run("b", "alpha", 1), ended(O.SUCCEEDED)),
        (run("c", "alpha", 2), ended(O.FAILED)),
        (run("d", "alpha", 2), ended(O.SUCCEEDED)),
    ]
    stats = summarise(rows)
    assert [s.workflow for s in stats] == ["alpha", "zeta"]
    alpha = stats[0]
    assert {v: (c.runs, c.failed) for v, c in alpha.versions.items()} == {1: (1, 0), 2: (2, 1)}


def test_the_service_reads_every_stored_run() -> None:
    db = open_database(":memory:")
    store = RunStore(db)
    for i, outcome in enumerate((O.SUCCEEDED, O.FAILED)):
        store.create(f"r{i}", "demo", 1, {}, T)
        store.append(f"r{i}", ended(outcome))
    store.create("copy", "demo", 1, {}, T, origin="svc-1")  # a run of a service, not finished
    [stats] = StatisticsService(db).statistics()
    assert (stats.runs, stats.running, stats.failed) == (3, 1, 1)
