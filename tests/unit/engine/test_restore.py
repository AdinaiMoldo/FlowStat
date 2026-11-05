"""Tests of the restore of a run from its history and of the resolution of uncertain steps.

US-15 and US-16 (docs/dsl-spec.md 5.5). The history is produced by the simulated driver, cut at a
chosen event (the moment of the crash), restored into a new scheduler and continued by hand.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from flowstat.compiler.plan import Plan
from flowstat.engine.scheduler import Scheduler, Start
from flowstat.engine.simulation import Done, Fail, Result, Wait, simulate
from flowstat.model.events import Event, EventKind
from flowstat.model.states import RunOutcome, StepState
from flowstat.recovery.saga import compensation_blockers
from tests.unit.engine.helpers import doc, plan_of

S = StepState
K = EventKind

LINE = (
    "  a:\n    action: x.y\n"
    "  b:\n    action: x.y\n    needs: [a]\n"
    "  c:\n    action: x.y\n    needs: [b]\n"
)
SEND = "  a:\n    action: x.send\n  b:\n    action: x.y\n  d:\n    action: x.y\n    needs: [a]\n"
SAGA = (
    "  a:\n    action: x.y\n    compensate: x.undo_a\n"
    "  b:\n    action: x.y\n    needs: [a]\n    compensate: x.undo_b\n"
    "  c:\n    action: x.y\n    needs: [b]\n    on_failure: compensate\n"
)


def always(action: str) -> bool:
    return True


def all_but_send(action: str) -> bool:
    return action != "x.send"


def line() -> Plan:
    return plan_of(doc(LINE, "goals: {any_of: [c]}\n"))


def history(
    plan: Plan, workers: int = 1, behavior: Callable[[Start], Result] | None = None
) -> list[Event]:
    sim = simulate(plan, {}, workers, behavior, compensation_planner=compensation_blockers)
    return sim.scheduler.events


def until(
    events: list[Event], kind: EventKind, step: str | None = None, nth: int = 1
) -> list[Event]:
    """The history up to and including the nth event of this kind (and step): the crash."""
    seen = 0
    for position, event in enumerate(events):
        if event.kind is kind and (step is None or event.step == step):
            seen += 1
            if seen == nth:
                return events[: position + 1]
    raise AssertionError(f"no {kind} {step} #{nth}")


def restored(
    plan: Plan,
    events: list[Event],
    idempotent: Callable[[str], bool] = always,
    workers: int = 1,
    now: float = 100.0,
) -> tuple[Scheduler, list[Start]]:
    scheduler = Scheduler(plan, {}, workers, compensation_planner=compensation_blockers)
    commands = scheduler.restore(events, now, idempotent)
    assert all(isinstance(c, Start) for c in commands)
    return scheduler, [c for c in commands if isinstance(c, Start)]


# -- US-15: the story --------------------------------------------------------------------------
def test_a_finished_step_is_not_run_again_and_the_running_one_gets_the_same_attempt() -> None:
    # Given the engine was killed while b was RUNNING (a had succeeded)
    crashed = until(history(line()), K.STEP_STARTED, "b")

    # When it restarts
    scheduler, commands = restored(line(), crashed)

    # Then a is not run again, b runs once more with the same attempt (the same key), and the run
    # completes
    assert [(c.step, c.attempt) for c in commands] == [("b", 1)]
    assert scheduler.states["a"] is S.SUCCEEDED and scheduler.states["b"] is S.RUNNING
    more = scheduler.step_done("b", {}, 101.0)
    assert [c.step for c in more if isinstance(c, Start)] == ["c"]
    scheduler.step_done("c", {}, 102.0)
    assert scheduler.outcome is RunOutcome.SUCCEEDED


def test_the_restore_continues_the_sequence_and_says_that_it_happened() -> None:
    crashed = until(history(line()), K.STEP_STARTED, "b")

    scheduler, _ = restored(line(), crashed, now=55.0)

    assert [e.seq for e in scheduler.events] == list(range(len(scheduler.events)))
    assert scheduler.events[: len(crashed)] == crashed
    resumed = scheduler.events[len(crashed)]
    assert (resumed.kind, resumed.at) == (K.RUN_RESUMED, 55.0)


def test_the_arguments_of_the_running_step_are_rendered_again_from_the_recorded_outputs() -> None:
    steps = (
        '  a:\n    action: x.y\n  b:\n    action: x.y\n    needs: [a]\n    with: {n: "${a.n}"}\n'
    )
    plan = plan_of(doc(steps, "goals: {any_of: [b]}\n"))
    events = history(plan, behavior=lambda c: Done(1.0, {"n": 7}))

    _, commands = restored(plan, until(events, K.STEP_STARTED, "b"))

    assert commands[0].args == {"n": 7}


def test_a_retry_that_was_running_keeps_its_attempt_number() -> None:
    steps = "  a:\n    action: x.y\n    retry: {max: 3, backoff: fixed, delay: 2}\n"
    plan = plan_of(doc(steps, "goals: {any_of: [a]}\n"))
    fails = {"left": 2}

    def act(command: Start) -> Result:
        if fails["left"]:
            fails["left"] -= 1
            return Fail(1.0, "E-ACT-002", "lost connection", transient=True)
        return Done()

    crashed = until(history(plan, behavior=act), K.STEP_RETRY, "a", nth=2)

    scheduler, commands = restored(plan, crashed)

    assert [(c.step, c.attempt) for c in commands] == [("a", 3)]
    assert scheduler.states["a"] is S.RUNNING


def test_a_waiting_step_stays_waiting_and_can_still_be_answered() -> None:
    steps = "  a:\n    action: approval.wait\n  b:\n    action: x.y\n    needs: [a]\n"
    plan = plan_of(doc(steps, "goals: {any_of: [b]}\n"))
    events = history(plan, behavior=lambda c: Wait() if c.step == "a" else Done())

    scheduler, commands = restored(plan, until(events, K.STEP_WAITING, "a"))

    assert commands == [] and scheduler.states["a"] is S.WAITING
    assert scheduler.status == "WAITING"
    more = scheduler.signal("a", {"decision": "approved"}, 101.0)
    assert [c.step for c in more if isinstance(c, Start)] == ["b"]


def test_a_step_that_was_ready_but_had_no_events_of_its_own_is_started() -> None:
    crashed = until(history(line()), K.STEP_FINISHED, "a")

    scheduler, commands = restored(line(), crashed)

    assert [(c.step, c.attempt) for c in commands] == [("b", 1)]
    assert scheduler.states["b"] is S.RUNNING


def test_a_run_that_was_started_and_has_no_other_event_goes_on() -> None:
    crashed = until(history(line()), K.RUN_STARTED)

    _, commands = restored(line(), crashed)

    assert [c.step for c in commands] == ["a"]


def test_a_finished_run_stays_finished_and_logs_nothing() -> None:
    events = history(line())

    scheduler, commands = restored(line(), events)

    assert commands == [] and scheduler.outcome is RunOutcome.SUCCEEDED
    assert scheduler.events == events


def test_the_restore_does_not_exceed_the_limit_of_parallel_steps() -> None:
    steps = "".join(f"  s{i}:\n    action: x.y\n" for i in range(4))
    plan = plan_of(doc(steps, "goals: {any_of: [s0]}\n"))
    crashed = until(history(plan, workers=2), K.STEP_STARTED, nth=2)

    scheduler, commands = restored(plan, crashed, workers=2)

    assert len(commands) == 2 and scheduler.status == "RUNNING"


def test_a_stop_that_was_recorded_only_in_part_is_completed() -> None:
    steps = "  a:\n    action: x.y\n  b:\n    action: x.y\n  c:\n    action: x.y\n    needs: [a]\n"
    plan = plan_of(doc(steps, "goals: {any_of: [c]}\n"))

    def act(command: Start) -> Result:
        return Fail(1.0, "E-ACT-002", "broke") if command.step == "a" else Done(5.0)

    events = history(plan, workers=2, behavior=act)
    crashed = until(
        events, K.STEP_FINISHED, "a"
    )  # the failure is stored, the cancellations are not

    scheduler, commands = restored(plan, crashed, workers=2)

    assert commands == []
    assert scheduler.states["b"] is S.CANCELLED and scheduler.states["c"] is S.CANCELLED
    assert scheduler.outcome is RunOutcome.FAILED


def test_restoring_a_restored_history_gives_the_same_state() -> None:
    # a second crash during the recovery itself
    crashed = until(history(line()), K.STEP_STARTED, "b")
    first, _ = restored(line(), crashed, now=10.0)

    second, commands = restored(line(), first.events, now=20.0)

    assert [(c.step, c.attempt) for c in commands] == [("b", 1)]
    assert second.states == first.states


def test_restore_needs_a_new_scheduler() -> None:
    scheduler = Scheduler(line(), {}, 1)
    scheduler.start(0.0)

    with pytest.raises(ValueError, match="new scheduler"):
        scheduler.restore([], 1.0, always)


def test_a_history_of_another_workflow_is_refused() -> None:
    other = plan_of(doc("  z:\n    action: x.y\n", "goals: {any_of: [z]}\n"))
    events = history(other)

    with pytest.raises(ValueError, match="unknown step"):
        Scheduler(line(), {}, 1).restore(events, 1.0, always)


# -- US-16: uncertain steps --------------------------------------------------------------------
def send_plan() -> Plan:
    return plan_of(doc(SEND, "goals: {any_of: [d]}\n"))


def crashed_send() -> list[Event]:
    return until(history(send_plan(), workers=2), K.STEP_STARTED, nth=2)


def test_a_running_step_that_is_not_idempotent_becomes_uncertain() -> None:
    # Given the engine was killed while the non-idempotent step a was RUNNING (b ran beside it)
    # When it restarts
    scheduler, commands = restored(send_plan(), crashed_send(), all_but_send, workers=2)

    # Then a is UNCERTAIN, nothing of a is run again, b (independent) is run again
    assert scheduler.states["a"] is S.UNCERTAIN
    assert [c.step for c in commands] == ["b"]
    event = next(e for e in scheduler.events if e.kind is K.STEP_UNCERTAIN)
    assert (event.step, event.code, event.state) == ("a", "E-REC-001", S.RUNNING)
    assert "outcome" in (event.message or "")


def test_steps_that_depend_on_an_uncertain_step_wait_and_the_run_waits() -> None:
    scheduler, _ = restored(send_plan(), crashed_send(), all_but_send, workers=2)

    scheduler.step_done("b", {}, 101.0)

    assert scheduler.states["d"] is S.PENDING
    assert scheduler.status == "WAITING" and scheduler.outcome is None


def test_the_operator_marks_an_uncertain_step_done() -> None:
    scheduler, _ = restored(send_plan(), crashed_send(), all_but_send, workers=2)
    scheduler.step_done("b", {}, 101.0)

    commands = scheduler.resolve("a", "done", 102.0, {"queued": 1})

    assert scheduler.states["a"] is S.SUCCEEDED and scheduler.outputs["a"] == {"queued": 1}
    assert [c.step for c in commands if isinstance(c, Start)] == ["d"]
    resolved = next(e for e in scheduler.events if e.kind is K.STEP_RESOLVED)
    assert (resolved.step, resolved.decision, resolved.at) == ("a", "done", 102.0)
    scheduler.step_done("d", {}, 103.0)
    assert scheduler.outcome is RunOutcome.SUCCEEDED


def test_done_without_an_output_gives_an_empty_one() -> None:
    scheduler, _ = restored(send_plan(), crashed_send(), all_but_send, workers=2)

    scheduler.resolve("a", "done", 102.0)

    assert scheduler.outputs["a"] == {}


def test_the_operator_asks_for_a_retry_which_is_a_new_attempt() -> None:
    scheduler, _ = restored(send_plan(), crashed_send(), all_but_send, workers=2)

    commands = scheduler.resolve("a", "retry", 102.0)

    starts = [c for c in commands if isinstance(c, Start)]
    assert [(c.step, c.action, c.attempt, c.compensation) for c in starts] == [
        ("a", "x.send", 2, False)
    ]
    assert scheduler.states["a"] is S.RUNNING
    resolved = next(e for e in scheduler.events if e.kind is K.STEP_RESOLVED)
    assert (resolved.decision, resolved.attempt) == ("retry", 2)
    scheduler.step_done("a", {}, 103.0)
    assert scheduler.states.get("a") is S.SUCCEEDED


def test_a_retry_that_fails_can_be_resolved_again_after_another_crash() -> None:
    scheduler, _ = restored(send_plan(), crashed_send(), all_but_send, workers=2)
    scheduler.resolve("a", "retry", 102.0)

    again, commands = restored(send_plan(), scheduler.events, all_but_send, workers=2, now=110.0)

    assert again.states["a"] is S.UNCERTAIN
    assert [c.step for c in commands if c.step == "a"] == []
    event = [e for e in again.events if e.kind is K.STEP_UNCERTAIN][-1]
    assert event.attempt == 2


def test_resolving_a_step_that_is_not_uncertain_does_nothing() -> None:
    scheduler, _ = restored(send_plan(), crashed_send(), all_but_send, workers=2)

    assert scheduler.resolve("b", "done", 102.0) == []
    assert scheduler.resolve("nope", "done", 102.0) == []
    assert scheduler.states["b"] is S.RUNNING


def test_an_unknown_decision_is_refused() -> None:
    scheduler, _ = restored(send_plan(), crashed_send(), all_but_send, workers=2)

    with pytest.raises(ValueError, match="done or retry"):
        scheduler.resolve("a", "maybe", 102.0)


def test_an_output_that_breaks_the_contract_fails_the_step() -> None:
    steps = "  a:\n    action: x.send\n    ensures: '${a.queued} > 0'\n"
    plan = plan_of(doc(steps, "goals: {any_of: [a]}\n"))
    crashed = until(history(plan), K.STEP_STARTED, "a")
    scheduler, _ = restored(plan, crashed, all_but_send)

    scheduler.resolve("a", "done", 102.0, {"queued": 0})

    assert scheduler.states["a"] is S.FAILED
    assert scheduler.errors["a"][0] == "E-CONTRACT-001"


def test_cancelling_the_run_cancels_an_uncertain_step() -> None:
    scheduler, _ = restored(send_plan(), crashed_send(), all_but_send, workers=2)

    scheduler.cancel(102.0)

    assert scheduler.states["a"] is S.CANCELLED
    assert scheduler.outcome is RunOutcome.CANCELLED


def test_a_failure_elsewhere_stops_the_run_and_the_uncertain_step_is_cancelled() -> None:
    scheduler, _ = restored(send_plan(), crashed_send(), all_but_send, workers=2)

    scheduler.step_failed("b", "E-ACT-002", "broke", 102.0)

    assert scheduler.states["a"] is S.CANCELLED
    assert scheduler.outcome is RunOutcome.FAILED


def test_an_uncertain_step_is_not_decided_again_by_a_second_restore() -> None:
    scheduler, _ = restored(send_plan(), crashed_send(), all_but_send, workers=2)
    scheduler.step_done("b", {}, 101.0)

    again, commands = restored(send_plan(), scheduler.events, all_but_send, workers=2)

    assert commands == [] and again.states["a"] is S.UNCERTAIN and again.status == "WAITING"


# -- recovery during compensation --------------------------------------------------------------
def saga_plan() -> Plan:
    return plan_of(doc(SAGA, "goals: {any_of: [c]}\n"))


def saga_history() -> list[Event]:
    def act(command: Start) -> Result:
        if command.compensation:
            return Done()
        return Fail(1.0, "E-ACT-002", "c broke") if command.step == "c" else Done()

    return history(saga_plan(), behavior=act)


def test_a_run_that_was_stopped_before_its_compensation_began_begins_it() -> None:
    crashed = until(saga_history(), K.STEP_FINISHED, "c")  # c failed; nothing else was stored

    scheduler, commands = restored(saga_plan(), crashed)

    assert scheduler.status == "COMPENSATING"
    assert [(c.step, c.action, c.compensation) for c in commands] == [("b", "x.undo_b", True)]


def test_a_compensation_that_was_running_is_run_again_with_the_same_attempt() -> None:
    crashed = until(saga_history(), K.STEP_COMPENSATING, "b")

    scheduler, commands = restored(saga_plan(), crashed)

    assert [(c.step, c.attempt, c.compensation) for c in commands] == [("b", 1, True)]
    more = scheduler.step_done("b", {}, 101.0)
    assert [(c.step, c.compensation) for c in more if isinstance(c, Start)] == [("a", True)]
    scheduler.step_done("a", {}, 102.0)
    assert scheduler.outcome is RunOutcome.COMPENSATED


def test_a_finished_compensation_is_not_repeated() -> None:
    events = saga_history()
    crashed = until(events, K.STEP_FINISHED, "b", nth=2)  # b's undo finished

    scheduler, commands = restored(saga_plan(), crashed)

    assert scheduler.states["b"] is S.COMPENSATED
    assert [(c.step, c.action) for c in commands] == [("a", "x.undo_a")]


def test_a_failed_compensation_keeps_the_steps_behind_it_uncompensated_after_a_restore() -> None:
    def act(command: Start) -> Result:
        if command.compensation:
            return Fail(1.0, "E-ACT-002", "undo broke") if command.step == "b" else Done()
        return Fail(1.0, "E-ACT-002", "c broke") if command.step == "c" else Done()

    events = history(saga_plan(), behavior=act)
    crashed = until(events, K.STEP_FINISHED, "b", nth=2)

    scheduler, commands = restored(saga_plan(), crashed)

    assert commands == []
    assert scheduler.states["b"] is S.COMPENSATION_FAILED and scheduler.states["a"] is S.SUCCEEDED
    assert scheduler.outcome is RunOutcome.COMPENSATION_FAILED


def test_a_compensation_that_is_not_idempotent_becomes_uncertain() -> None:
    crashed = until(saga_history(), K.STEP_COMPENSATING, "b")

    scheduler, commands = restored(saga_plan(), crashed, lambda action: action != "x.undo_b")

    assert commands == [] and scheduler.states["b"] is S.UNCERTAIN
    assert scheduler.status == "WAITING"
    event = next(e for e in scheduler.events if e.kind is K.STEP_UNCERTAIN)
    assert event.state is S.COMPENSATING


def test_an_uncertain_compensation_marked_done_lets_the_next_one_go() -> None:
    crashed = until(saga_history(), K.STEP_COMPENSATING, "b")
    scheduler, _ = restored(saga_plan(), crashed, lambda action: action != "x.undo_b")

    commands = scheduler.resolve("b", "done", 101.0)

    assert scheduler.states["b"] is S.COMPENSATED
    assert [(c.step, c.compensation) for c in commands if isinstance(c, Start)] == [("a", True)]
    scheduler.step_done("a", {}, 102.0)
    assert scheduler.outcome is RunOutcome.COMPENSATED


def test_an_uncertain_compensation_retried_is_a_new_attempt_of_the_undo() -> None:
    crashed = until(saga_history(), K.STEP_COMPENSATING, "b")
    scheduler, _ = restored(saga_plan(), crashed, lambda action: action != "x.undo_b")

    commands = scheduler.resolve("b", "retry", 101.0)

    starts = [c for c in commands if isinstance(c, Start)]
    assert [(c.step, c.action, c.attempt, c.compensation) for c in starts] == [
        ("b", "x.undo_b", 2, True)
    ]
    assert scheduler.states["b"] is S.COMPENSATING


def test_the_run_does_not_finish_while_a_compensation_is_uncertain() -> None:
    crashed = until(saga_history(), K.STEP_COMPENSATING, "b")
    scheduler, _ = restored(saga_plan(), crashed, lambda action: action != "x.undo_b")

    assert scheduler.outcome is None
