"""The time limit of a step that waits for a person (US-38, docs/dsl-spec.md section 5.6).

A step with ``timeout: T`` that went to WAITING at time W fails at W + T with E-RUN-009 if nobody
answered. The deadline comes from the recorded time of the wait, so it is the same after a
restore, whatever time the restore is made at. The failure is the failure of any step: its
``on_failure`` decides what the run does.
"""

from __future__ import annotations

import pytest

from flowstat.engine.scheduler import TIMEOUT_CODE, Scheduler
from flowstat.model.events import Event, EventKind
from flowstat.model.states import RunOutcome, StepState
from flowstat.recovery.saga import compensation_blockers
from tests.unit.engine.helpers import doc, plan_of

S = StepState
K = EventKind
CODE = TIMEOUT_CODE


def workflow(timeout: str = "100", on_failure: str = "fail", tail: bool = True):  # type: ignore[no-untyped-def]
    steps = (
        "  load:\n    action: x.y\n"
        f"  ask:\n    action: x.ask\n    needs: [load]\n    timeout: {timeout}\n"
        f"    on_failure: {on_failure}\n"
        + ("  after:\n    action: x.y\n    needs: [ask]\n    join: all_done\n" if tail else "")
    )
    return plan_of(doc(steps, "goals: {any_of: [ask]}\n"))


def waiting(plan=None, at: float = 10.0) -> Scheduler:  # type: ignore[no-untyped-def]
    """A run whose step ``ask`` went to WAITING at time ``at``."""
    s = Scheduler(plan or workflow(), {}, 2)
    s.start(0.0)
    s.step_done("load", {}, 1.0)
    s.step_waiting("ask", at)
    assert s.states["ask"] is S.WAITING
    return s


def test_the_deadline_is_the_time_of_the_wait_plus_the_limit() -> None:
    assert waiting(at=10.0).deadlines() == {"ask": 110.0}


def test_a_step_without_a_limit_has_no_deadline() -> None:
    plan = plan_of(doc("  ask:\n    action: x.ask\n", "goals: {any_of: [ask]}\n"))
    s = Scheduler(plan, {}, 1)
    s.start(0.0)
    s.step_waiting("ask", 5.0)
    assert s.deadlines() == {} and s.expire(1e9) == []
    assert s.states["ask"] is S.WAITING


def test_nothing_happens_before_the_deadline() -> None:
    s = waiting()
    assert s.expire(109.999) == [] and s.states["ask"] is S.WAITING
    assert s.outcome is None


def test_at_the_deadline_the_step_fails_with_its_own_code() -> None:
    s = waiting()
    s.expire(110.0)
    assert s.states["ask"] is S.FAILED
    assert s.errors["ask"][0] == CODE and "100" in s.errors["ask"][1]
    [finished] = [e for e in s.events if e.kind is K.STEP_FINISHED and e.step == "ask"]
    assert finished.at == 110.0 and finished.code == CODE


def test_the_time_given_to_expire_is_the_time_of_the_failure_and_the_run_ends_then() -> None:
    s = waiting(workflow(tail=False))
    s.expire(500.0)  # the check came late: the failure is recorded when it was noticed
    assert s.outcome is RunOutcome.FAILED
    assert s.events[-1].kind is K.RUN_FINISHED and s.events[-1].at == 500.0


def test_the_step_that_is_answered_in_time_does_not_expire() -> None:
    s = waiting()
    s.signal("ask", {"decision": "approved"}, 50.0)
    assert s.states["ask"] is S.SUCCEEDED
    assert s.expire(1e6) == [] and s.deadlines() == {}


def test_an_answer_after_the_failure_is_ignored() -> None:
    s = waiting()
    s.expire(110.0)
    assert s.signal("ask", {"decision": "approved"}, 120.0) == []
    assert s.states["ask"] is S.FAILED


def test_on_failure_continue_lets_the_steps_after_it_run() -> None:
    s = waiting(workflow(on_failure="continue"))
    commands = s.expire(110.0)
    assert s.states["ask"] is S.FAILED and s.outcome is None
    assert [c.step for c in commands if hasattr(c, "step")] == ["after"]  # all_done: it is ready


def test_on_failure_compensate_undoes_what_was_done() -> None:
    steps = (
        "  load:\n    action: x.y\n    compensate: x.undo\n"
        "  ask:\n    action: x.ask\n    needs: [load]\n    timeout: 5\n"
        "    on_failure: compensate\n"
    )
    plan = plan_of(doc(steps, "goals: {any_of: [ask]}\n"))
    s = Scheduler(plan, {}, 2, compensation_planner=compensation_blockers)
    s.start(0.0)
    s.step_done("load", {}, 1.0)
    s.step_waiting("ask", 2.0)
    commands = s.expire(7.0)
    undo = [c for c in commands if getattr(c, "compensation", False)]
    assert [c.step for c in undo] == ["load"]


def test_a_retry_is_never_given_for_a_time_limit() -> None:
    steps = "  ask:\n    action: x.ask\n    timeout: 5\n    retry: {max: 3}\n"
    s = Scheduler(plan_of(doc(steps, "goals: {any_of: [ask]}\n")), {}, 1)
    s.start(0.0)
    s.step_waiting("ask", 1.0)
    s.expire(6.0)
    assert s.states["ask"] is S.FAILED
    assert not [e for e in s.events if e.kind is K.STEP_RETRY]


def test_several_steps_that_pass_their_limit_together_all_fail_in_plan_order() -> None:
    steps = (
        "  a:\n    action: x.ask\n    timeout: 5\n    on_failure: continue\n"
        "  b:\n    action: x.ask\n    timeout: 3\n    on_failure: continue\n"
    )
    s = Scheduler(plan_of(doc(steps, "goals: {any_of: [a, b]}\n")), {}, 2)
    s.start(0.0)
    s.step_waiting("a", 0.0)
    s.step_waiting("b", 0.0)
    assert s.deadlines() == {"a": 5.0, "b": 3.0}
    s.expire(10.0)
    assert (s.states["a"], s.states["b"]) == (S.FAILED, S.FAILED)
    fails = [e.step for e in s.events if e.kind is K.STEP_FINISHED]
    assert fails == ["a", "b"]


# -- the deadline survives a restart -------------------------------------------------------------
def test_after_a_restore_the_deadline_is_the_same() -> None:
    before = waiting(at=10.0)
    now = 9999.0  # the process came back long after the limit
    after = Scheduler(workflow(), {}, 2)
    after.restore(before.events, now, lambda action: True)
    assert after.deadlines() == {"ask": 110.0}  # from the recorded time, not from ``now``
    assert after.states["ask"] is S.WAITING  # restoring does not decide; the check does
    after.expire(now)
    assert str(after.states["ask"].value) == "FAILED" and after.errors["ask"][0] == CODE


def test_a_restore_that_is_made_before_the_deadline_expires_nothing() -> None:
    before = waiting(at=10.0)
    after = Scheduler(workflow(), {}, 2)
    after.restore(before.events, 20.0, lambda action: True)
    assert after.expire(20.0) == [] and after.states["ask"] is S.WAITING


def test_the_failure_by_time_survives_a_second_restore() -> None:
    first = waiting(at=10.0)
    first.expire(200.0)
    second = Scheduler(workflow(), {}, 2)
    second.restore(first.events, 300.0, lambda action: True)
    assert second.states["ask"] is S.FAILED and second.outcome is RunOutcome.FAILED
    assert second.deadlines() == {}


def test_the_deadline_counts_from_the_wait_that_the_history_records() -> None:
    events = [
        Event(0, K.RUN_STARTED, 0.0),
        Event(1, K.STEP_STARTED, 1.0, step="load"),
        Event(2, K.STEP_FINISHED, 1.5, step="load", state=S.SUCCEEDED, output={}),
        Event(3, K.STEP_STARTED, 2.0, step="ask"),
        Event(4, K.STEP_WAITING, 3.0, step="ask"),
    ]
    s = Scheduler(workflow(), {}, 2)
    s.restore(events, 4.0, lambda action: True)
    assert s.deadlines() == {"ask": 103.0}


# -- the same as a failure that the driver reports ---------------------------------------------
@pytest.mark.parametrize("on_failure", ["fail", "continue"])
def test_expiring_is_the_same_as_failing_the_step_with_that_code(on_failure: str) -> None:
    by_time = waiting(workflow(on_failure=on_failure))
    by_hand = waiting(workflow(on_failure=on_failure))
    by_time.expire(110.0)
    by_hand.step_failed("ask", CODE, by_time.errors["ask"][1], 110.0)
    assert by_time.events == by_hand.events
