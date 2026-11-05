"""Tests of the thread-pool driver: real threads around the same scheduler as the simulation."""

from __future__ import annotations

import itertools
import threading
from collections.abc import Callable, Mapping

from flowstat.engine.runner import Error, Ok, Outcome, Pause, run_threaded
from flowstat.engine.scheduler import Scheduler, Start
from flowstat.model.events import EventKind
from flowstat.model.states import RunOutcome, StepState
from tests.unit.engine.helpers import doc, plan_of

S = StepState
TWO_THEN_ONE = (
    "  a:\n    action: x.y\n  b:\n    action: x.y\n  c:\n    action: x.y\n    needs: [a, b]\n"
)


def no_sleep(seconds: float) -> None:
    """The tests of the driver need no real waiting."""


def counter() -> Callable[[], float]:
    ticks = itertools.count()
    return lambda: float(next(ticks))


def scheduler(steps: str, goals: str, workers: int = 4) -> Scheduler:
    return Scheduler(plan_of(doc(steps, goals)), {}, workers)


def test_independent_steps_really_run_at_the_same_time() -> None:
    barrier = threading.Barrier(2, timeout=5)  # both steps must be inside at once to pass

    def execute(command: Start) -> Outcome:
        if command.step in {"a", "b"}:
            barrier.wait()
        return Ok({})

    s = scheduler(TWO_THEN_ONE, "goals: {any_of: [c]}\n")
    run_threaded(s, execute, counter(), no_sleep)
    assert s.outcome is RunOutcome.SUCCEEDED
    assert all(state is S.SUCCEEDED for state in s.states.values())


def test_never_more_steps_run_than_workers() -> None:
    lock = threading.Lock()
    active = peak = 0

    def execute(command: Start) -> Outcome:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        threading.Event().wait(0.01)
        with lock:
            active -= 1
        return Ok({})

    steps = "".join(f"  s{i}:\n    action: x.y\n" for i in range(8))
    s = scheduler(steps, "goals: {any_of: [s0]}\n", workers=2)
    run_threaded(s, execute, counter(), no_sleep)
    assert peak <= 2 and s.outcome is RunOutcome.SUCCEEDED


def test_a_failed_step_is_reported_with_code_and_message() -> None:
    def execute(command: Start) -> Outcome:
        return Error("E-ACT-002", "cannot read x") if command.step == "a" else Ok({})

    steps = "  a:\n    action: x.y\n    on_failure: continue\n  b:\n    action: x.y\n"
    s = scheduler(steps, "goals: {any_of: [b]}\n")
    run_threaded(s, execute, counter(), no_sleep)
    assert s.errors["a"] == ("E-ACT-002", "cannot read x")
    assert s.outcome is RunOutcome.SUCCEEDED


def test_an_exception_inside_an_action_fails_only_that_step() -> None:
    def execute(command: Start) -> Outcome:
        if command.step == "a":
            raise RuntimeError("boom")
        return Ok({})

    steps = "  a:\n    action: x.y\n    on_failure: continue\n  b:\n    action: x.y\n"
    s = scheduler(steps, "goals: {any_of: [b]}\n")
    run_threaded(s, execute, counter(), no_sleep)
    assert s.states["a"] is S.FAILED
    assert s.errors["a"] == ("E-ACT-999", "RuntimeError: boom")
    assert s.states["b"] is S.SUCCEEDED


def test_a_failure_that_stops_the_run_cancels_the_rest() -> None:
    release = threading.Event()

    def execute(command: Start) -> Outcome:
        if command.step == "slow":
            release.wait(5)
            return Ok({})
        release.set()
        return Error("E-ACT-002", "bad")

    steps = (
        "  slow:\n    action: x.y\n  bad:\n    action: x.y\n"
        "  after:\n    action: x.y\n    needs: [bad]\n"
    )
    s = scheduler(steps, "goals: {any_of: [after]}\n")
    run_threaded(s, execute, counter(), no_sleep)
    assert s.outcome is RunOutcome.FAILED
    assert (s.states["bad"], s.states["after"]) == (S.FAILED, S.CANCELLED)
    assert s.states["slow"] is S.CANCELLED  # its late result is ignored


def waiting_workflow() -> Scheduler:
    steps = (
        "  ask:\n    action: approval.wait\n"
        "  go:\n    action: x.y\n    needs: [ask]\n    when: \"${ask.decision} == 'approved'\"\n"
    )
    return scheduler(steps, "goals: {any_of: [go]}\n")


def test_a_waiting_step_is_answered_and_the_run_finishes() -> None:
    def execute(command: Start) -> Outcome:
        return Pause() if command.step == "ask" else Ok({})

    def answer(command: Start) -> Mapping[str, object] | None:
        return {"decision": "approved"}

    s = waiting_workflow()
    run_threaded(s, execute, counter(), no_sleep, answer)
    assert s.outcome is RunOutcome.SUCCEEDED
    kinds = [e.kind for e in s.events if e.step == "ask"]
    assert kinds == [EventKind.STEP_STARTED, EventKind.STEP_WAITING, EventKind.STEP_FINISHED]


def test_a_rejection_ends_without_reaching_the_goal() -> None:
    def execute(command: Start) -> Outcome:
        return Pause() if command.step == "ask" else Ok({})

    s = waiting_workflow()
    run_threaded(s, execute, counter(), no_sleep, lambda c: {"decision": "rejected"})
    assert s.outcome is RunOutcome.GOAL_NOT_REACHED and s.states["go"] is S.SKIPPED


def test_without_an_answer_the_run_stays_waiting() -> None:
    s = waiting_workflow()
    run_threaded(s, lambda c: Pause() if c.step == "ask" else Ok({}), counter(), no_sleep)
    assert s.outcome is None and s.status == "WAITING"
    assert s.states["ask"] is S.WAITING


def test_times_come_from_the_given_clock() -> None:
    s = scheduler(TWO_THEN_ONE, "goals: {any_of: [c]}\n", workers=1)
    run_threaded(s, lambda c: Ok({}), counter(), no_sleep)
    times = [e.at for e in s.events]
    assert times == sorted(times) and len(set(times)) > 1
    assert all(float(t).is_integer() for t in times)


# -- write-ahead and recovery (US-15) ----------------------------------------------------------
def test_the_events_are_committed_before_the_commands_run() -> None:
    s = scheduler(TWO_THEN_ONE, "goals: {any_of: [c]}\n", workers=2)
    committed: list[list[object]] = []
    checked: list[bool] = []

    def execute(command: Start) -> Outcome:
        seen = committed[-1]  # the last commit before this call
        checked.append(any(getattr(e, "step", None) == command.step for e in seen))
        return Ok({})

    run_threaded(s, execute, counter(), no_sleep, journal=lambda: committed.append(list(s.events)))

    assert checked == [True, True, True]
    assert s.outcome is RunOutcome.SUCCEEDED
    assert len(committed[-1]) == len(s.events)  # everything is committed at the end


def test_the_journal_is_called_once_per_step_of_the_scheduler_and_before_dispatch() -> None:
    s = scheduler("  a:\n    action: x.y\n", "goals: {any_of: [a]}\n", workers=1)
    log: list[str] = []

    def execute(command: Start) -> Outcome:
        log.append("run")
        return Ok({})

    run_threaded(s, execute, counter(), no_sleep, journal=lambda: log.append("commit"))

    assert log == ["commit", "run", "commit"]


def test_a_run_goes_on_from_the_commands_of_a_restore() -> None:
    steps = "  a:\n    action: x.y\n  b:\n    action: x.y\n    needs: [a]\n"
    plan = plan_of(doc(steps, "goals: {any_of: [b]}\n"))
    first = Scheduler(plan, {}, 1)
    first.start(0.0)
    first.step_done("a", {}, 1.0)  # b has started; the engine dies here
    again = Scheduler(plan, {}, 1)
    commands = again.restore(first.events, 5.0, lambda action: True)
    ran: list[tuple[str, int]] = []

    def execute(command: Start) -> Outcome:
        ran.append((command.step, command.attempt))
        return Ok({})

    run_threaded(again, execute, counter(), no_sleep, commands=commands)

    assert ran == [("b", 1)] and again.outcome is RunOutcome.SUCCEEDED
