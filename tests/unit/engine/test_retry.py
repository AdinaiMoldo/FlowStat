"""Tests of retries (US-13): which failures are retried, how long the step waits, who sees it."""

from __future__ import annotations

import threading
from collections.abc import Callable

import pytest

from flowstat.engine.runner import Error, Ok, Outcome, run_threaded
from flowstat.engine.scheduler import Scheduler, Start
from flowstat.engine.simulation import Done, Fail, Result, simulate
from flowstat.model.events import EventKind
from flowstat.model.states import RunOutcome, StepState
from tests.unit.engine.helpers import doc, plan_of

S = StepState


def workflow(retry: str = "{max: 3, backoff: exponential}", extra: str = ""):  # type: ignore[no-untyped-def]
    steps = f"  a:\n    action: x.y\n    retry: {retry}\n{extra}"
    return plan_of(doc(steps, "goals: {any_of: [a]}\n"))


def retries(s: Scheduler) -> list[tuple[int | None, float | None]]:
    return [(e.attempt, e.delay) for e in s.events if e.kind is EventKind.STEP_RETRY]


# -- the scheduler ------------------------------------------------------------------------------
def test_the_first_attempt_has_number_one_and_no_delay() -> None:
    [first] = Scheduler(workflow(), {}, 1).start(0.0)
    assert isinstance(first, Start) and (first.attempt, first.delay) == (1, 0.0)


def test_a_transient_failure_gives_the_same_step_another_attempt_after_a_delay() -> None:
    s = Scheduler(workflow(), {}, 1)
    s.start(0.0)
    [again] = s.step_failed("a", "E-ACT-008", "connection lost", 1.0, transient=True)
    assert isinstance(again, Start)
    assert (again.step, again.attempt, again.delay) == ("a", 2, 1.0)
    assert s.states["a"] is S.RUNNING and "a" not in s.errors
    [event] = [e for e in s.events if e.kind is EventKind.STEP_RETRY]
    assert (event.step, event.at, event.attempt, event.delay) == ("a", 1.0, 2, 1.0)
    assert (event.code, event.message) == ("E-ACT-008", "connection lost")


def test_the_retry_carries_the_same_arguments_as_the_first_attempt() -> None:
    steps = "  a:\n    action: x.y\n    with: {p: '${inputs.q}'}\n    retry: {max: 1}\n"
    plan = plan_of(doc(steps, "goals: {any_of: [a]}\n", inputs="  q: {type: string}\n"))
    s = Scheduler(plan, {"q": "v"}, 1)
    [first] = s.start(0.0)
    [again] = s.step_failed("a", "E-ACT-008", "x", 1.0, transient=True)
    assert isinstance(first, Start) and isinstance(again, Start)
    assert again.args == first.args == {"p": "v"} and again.action == first.action


def test_a_permanent_failure_is_not_retried() -> None:
    s = Scheduler(workflow(), {}, 1)
    s.start(0.0)
    assert s.step_failed("a", "E-ACT-002", "cannot read x", 1.0) == []
    assert s.states["a"] is S.FAILED and retries(s) == []


def test_a_step_without_retry_fails_at_once_even_when_the_error_is_transient() -> None:
    s = Scheduler(plan_of(doc("  a:\n    action: x.y\n", "goals: {any_of: [a]}\n")), {}, 1)
    s.start(0.0)
    s.step_failed("a", "E-ACT-008", "lost", 1.0, transient=True)
    assert s.states["a"] is S.FAILED


def test_the_step_keeps_its_slot_while_it_waits_for_the_retry() -> None:
    steps = "  a:\n    action: x.y\n    retry: {max: 1}\n  b:\n    action: x.y\n    needs: []\n"
    s = Scheduler(plan_of(doc(steps, "goals: {all_of: [a, b]}\n")), {}, 1)
    s.start(0.0)  # one slot: only a runs
    commands = s.step_failed("a", "E-ACT-008", "x", 1.0, transient=True)
    assert [c.step for c in commands if isinstance(c, Start)] == ["a"]
    assert s.states["b"] is S.READY


def test_a_failed_contract_is_not_retried() -> None:
    steps = "  a:\n    action: x.y\n    ensures: ${a.n} > 5\n    retry: {max: 3}\n"
    s = Scheduler(plan_of(doc(steps, "goals: {any_of: [a]}\n")), {}, 1)
    s.start(0.0)
    s.step_done("a", {"n": 1}, 1.0)
    assert s.states["a"] is S.FAILED and s.errors["a"][0] == "E-CONTRACT-001"
    assert retries(s) == []


def test_a_waiting_step_is_not_retried() -> None:
    s = Scheduler(workflow(), {}, 1)
    s.start(0.0)
    s.step_waiting("a", 1.0)
    s.step_failed("a", "E-ACT-008", "x", 2.0, transient=True)
    assert s.states["a"] is S.FAILED


def test_a_late_failure_of_a_finished_step_is_ignored() -> None:
    s = Scheduler(workflow(), {}, 1)
    s.start(0.0)
    s.step_done("a", {}, 1.0)
    assert s.step_failed("a", "E-ACT-008", "x", 2.0, transient=True) == []
    assert s.states["a"] is S.SUCCEEDED


# -- every combination of max, backoff and number of failures ------------------------------------
@pytest.mark.parametrize("backoff", ["fixed", "exponential"])
@pytest.mark.parametrize("limit", [0, 1, 2, 3])
@pytest.mark.parametrize("failures", [0, 1, 2, 3, 4])
def test_the_step_succeeds_when_it_fails_at_most_max_times(
    backoff: str, limit: int, failures: int
) -> None:
    plan = workflow(f"{{max: {limit}, backoff: {backoff}, delay: 0.5}}")

    def act(command: Start) -> Result:
        if command.attempt <= failures:
            return Fail(1.0, "E-ACT-008", "lost", transient=True)
        return Done(1.0, {})

    sim = simulate(plan, {}, 1, act)
    used = min(failures, limit)
    expected_delays = [0.5 * 2**k if backoff == "exponential" else 0.5 for k in range(used)]
    assert [d for _, d in retries(sim.scheduler)] == expected_delays
    assert [a for a, _ in retries(sim.scheduler)] == list(range(2, used + 2))
    if failures <= limit:
        assert sim.scheduler.states["a"] is S.SUCCEEDED
        assert sim.scheduler.outcome is RunOutcome.SUCCEEDED
    else:
        assert sim.scheduler.states["a"] is S.FAILED
        assert sim.scheduler.errors["a"] == ("E-ACT-008", "lost")  # the last error, unchanged
        assert sim.scheduler.outcome is RunOutcome.FAILED
    assert len(sim.attempts["a"]) == used + 1


def test_attempts_start_after_their_delay_on_the_virtual_clock() -> None:
    def act(command: Start) -> Result:
        if command.attempt < 3:
            return Fail(1.0, "E-ACT-008", "lost", transient=True)
        return Done(1.0, {})

    sim = simulate(workflow(), {}, 1, act)
    # attempt 1 at 0 fails at 1; attempt 2 starts after 1 s of delay (2), fails at 3;
    # attempt 3 starts after 2 s (5) and ends at 6
    assert sim.attempts["a"] == [0.0, 2.0, 5.0] and sim.makespan == 6.0


def test_a_failure_that_exhausts_the_retries_stops_the_run_by_the_policy() -> None:
    steps = "  a:\n    action: x.y\n    retry: {max: 1}\n  b:\n    action: x.y\n    needs: [a]\n"
    plan = plan_of(doc(steps, "goals: {any_of: [b]}\n"))
    sim = simulate(plan, {}, 2, lambda c: Fail(1.0, "E-ACT-008", "lost", transient=True))
    assert sim.scheduler.states["a"] is S.FAILED and sim.scheduler.states["b"] is S.CANCELLED
    assert sim.scheduler.outcome is RunOutcome.FAILED


# -- the thread driver ----------------------------------------------------------------------------
def run(plan_retry: str, execute: Callable[[Start], Outcome]) -> tuple[Scheduler, list[float]]:
    s = Scheduler(workflow(plan_retry), {}, 2)
    waits: list[float] = []
    lock = threading.Lock()

    def sleep(seconds: float) -> None:
        with lock:
            waits.append(seconds)

    run_threaded(s, execute, lambda: 0.0, sleep)
    return s, waits


def test_the_driver_waits_the_delay_before_every_retry_and_not_before_the_first_attempt() -> None:
    def execute(command: Start) -> Outcome:
        return Error("E-ACT-008", "lost", transient=True) if command.attempt < 3 else Ok({})

    s, waits = run("{max: 3, backoff: exponential}", execute)
    assert s.states["a"] is S.SUCCEEDED and waits == [1.0, 2.0]


def test_the_driver_gives_up_after_the_last_retry() -> None:
    s, waits = run("{max: 2, backoff: fixed, delay: 0.1}", lambda c: Error("E-ACT-008", "x", True))
    assert s.states["a"] is S.FAILED and waits == [0.1, 0.1]


def test_the_driver_does_not_wait_for_a_permanent_failure() -> None:
    s, waits = run("{max: 3}", lambda c: Error("E-ACT-002", "cannot read x"))
    assert s.states["a"] is S.FAILED and waits == []
