"""Acceptance tests of US-06 to US-10: the criteria of the stories, run on the simulated clock."""

from __future__ import annotations

from flowstat.engine.scheduler import Start
from flowstat.engine.simulation import Done, Fail, Result, Wait, simulate
from flowstat.model.states import RunOutcome, StepState
from tests.unit.engine.helpers import REGIONS, doc, example_plan, plan_of

S = StepState


def test_us06_independent_steps_overlap_and_the_join_starts_after_both() -> None:
    steps = (
        "  a:\n    action: x.y\n  b:\n    action: x.y\n  c:\n    action: x.y\n    needs: [a, b]\n"
    )
    sim = simulate(plan_of(doc(steps, "goals: {any_of: [c]}\n")), {}, 2, durations={"a": 3, "b": 2})
    assert sim.started["a"] == sim.started["b"] == 0.0  # they overlap in time
    assert sim.started["c"] == max(sim.finished["a"], sim.finished["b"]) == 3.0
    assert sim.scheduler.outcome is RunOutcome.SUCCEEDED
    assert sim.makespan == 4.0  # 3 for the longer branch, 1 for c; one worker would need 6


def test_us07_false_condition_skips_all_success_followers_but_not_all_done() -> None:
    steps = (
        "  b:\n    action: x.y\n    when: 1 > 2\n"
        "  c:\n    action: x.y\n    needs: [b]\n"
        "  d:\n    action: x.y\n    needs: [b]\n    join: all_done\n"
    )
    sim = simulate(plan_of(doc(steps, "goals: {any_of: [d]}\n")), {}, 2)
    states = sim.scheduler.states
    assert (states["b"], states["c"], states["d"]) == (S.SKIPPED, S.SKIPPED, S.SUCCEEDED)


def failing_loads(failing: set[str]):  # type: ignore[no-untyped-def]
    def act(command: Start) -> Result:
        if command.step in failing:
            return Fail(1.0, "E-ACT-002", f"cannot read {command.args['path']}")
        return Done(1.0, {"rows": [1, 2], "written": 2, "decision": "approved"})

    return act


def test_us07_quorum_two_failures_consolidate_runs() -> None:
    failing = {"load[BIS]", "load[OSC]"}
    sim = simulate(example_plan(), {"quarter": "2025Q1"}, 9, failing_loads(failing))
    states = sim.scheduler.states
    assert states["consolidate"] is S.SUCCEEDED
    assert states["check[BIS]"] is S.UPSTREAM_FAILED
    assert sim.scheduler.errors["load[BIS]"] == ("E-ACT-002", "cannot read BIS/2025Q1.xlsx")
    assert sim.scheduler.outputs["consolidate"] == {
        "rows": [1, 2],
        "written": 2,
        "decision": "approved",
    }


def test_us07_quorum_three_failures_is_upstream_failed() -> None:
    failing = {f"load[{r}]" for r in REGIONS[:3]}
    sim = simulate(example_plan(), {"quarter": "2025Q1"}, 9, failing_loads(failing))
    assert sim.scheduler.states["consolidate"] is S.UPSTREAM_FAILED
    assert sim.scheduler.outcome is RunOutcome.GOAL_NOT_REACHED


def approval_behavior(command: Start) -> Result:
    if command.action == "approval.wait":
        return Wait(0.5)
    return Done(1.0, {"rows": [1], "written": 1})


def test_us08_waiting_releases_the_slot_and_a_rejection_runs_the_other_branch() -> None:
    plan = example_plan()
    answers = {"approve": (10.0, {"decision": "rejected"})}
    sim = simulate(plan, {"quarter": "2025Q1"}, 1, approval_behavior, answers=answers)
    sch = sim.scheduler
    assert sch.states["approve"] is S.SUCCEEDED
    assert sch.outputs["approve"] == {"decision": "rejected"}  # a rejection is data
    assert sch.states["publish"] is S.SKIPPED
    assert sch.states["return_to_regions"] is S.SUCCEEDED
    assert sch.outcome is RunOutcome.SUCCEEDED  # US-09: the guarded branch handled the rejection


def test_us08_the_run_waits_without_an_answer() -> None:
    sim = simulate(example_plan(), {"quarter": "2025Q1"}, 1, approval_behavior)
    assert sim.scheduler.states["approve"] is S.WAITING
    assert sim.scheduler.status == "WAITING" and sim.scheduler.outcome is None


def test_us09_approved_run_publishes() -> None:
    answers = {"approve": (1.0, {"decision": "approved"})}
    sim = simulate(example_plan(), {"quarter": "2025Q1"}, 3, approval_behavior, answers=answers)
    assert sim.scheduler.states["publish"] is S.SUCCEEDED
    assert sim.scheduler.states["return_to_regions"] is S.SKIPPED
    assert sim.scheduler.outcome is RunOutcome.SUCCEEDED


def test_us09_no_goal_succeeded_is_goal_not_reached() -> None:
    answers = {"approve": (1.0, {"decision": "postponed"})}  # neither branch applies
    sim = simulate(example_plan(), {"quarter": "2025Q1"}, 3, approval_behavior, answers=answers)
    assert sim.scheduler.outcome is RunOutcome.GOAL_NOT_REACHED


def test_us10_contract_violation_fails_the_step() -> None:
    answers = {"approve": (1.0, {"decision": "approved"})}

    def act(command: Start) -> Result:
        if command.action == "approval.wait":
            return Wait(0.5)
        if command.step == "publish":
            return Done(1.0, {"written": 0})  # the action wrote 0 rows
        return Done(1.0, {"rows": [1], "written": 1})

    sim = simulate(example_plan(), {"quarter": "2025Q1"}, 3, act, answers=answers)
    sch = sim.scheduler
    assert sch.states["publish"] is S.FAILED
    code, message = sch.errors["publish"]
    assert code == "E-CONTRACT-001" and "written" in message
    assert sch.outcome is RunOutcome.FAILED
