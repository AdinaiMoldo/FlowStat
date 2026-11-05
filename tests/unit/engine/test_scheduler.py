"""Tests of the scheduler core (C3, level 1): a pure state machine, events in and commands out.

No threads and no sleeping: every test drives the scheduler by hand with explicit times.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from flowstat.engine.scheduler import Cancel, Scheduler, Start
from flowstat.engine.simulation import Done, Fail, Result, simulate
from flowstat.model.events import EventKind
from flowstat.model.states import RunOutcome, StepState
from tests.unit.engine.helpers import REGIONS, doc, example_plan, plan_of

S = StepState
A = "  a:\n    action: x.y\n"
B = "  b:\n    action: x.y\n"


def starts(commands: Sequence[object]) -> list[str]:
    return [c.step for c in commands if isinstance(c, Start)]


def make(
    steps: str,
    goals: str = "goals: {any_of: [z]}\n",
    m: int = 4,
    declared: str = "",
    inputs: dict[str, object] | None = None,
    durations: dict[str, float] | None = None,
) -> Scheduler:
    plan = plan_of(doc(steps, goals, declared))
    return Scheduler(plan, inputs or {}, m, durations=durations)


# -- US-06: parallel steps -----------------------------------------------------------------------
DIAMOND = A + B + "  z:\n    action: x.y\n    needs: [a, b]\n"


def test_independent_steps_start_together_and_the_join_waits_for_both() -> None:
    sch = make(DIAMOND, m=2)
    assert starts(sch.start(0.0)) == ["a", "b"]
    assert sch.states["z"] is S.PENDING
    assert sch.step_done("a", {}, 1.0) == []
    assert sch.states["z"] is S.PENDING
    assert starts(sch.step_done("b", {}, 2.0)) == ["z"]
    sch.step_done("z", {}, 3.0)
    assert sch.outcome is RunOutcome.SUCCEEDED


def test_at_most_m_steps_run_at_once() -> None:
    sch = make(DIAMOND, m=1)
    assert starts(sch.start(0.0)) == ["a"]
    assert starts(sch.step_done("a", {}, 1.0)) == ["b"]
    assert starts(sch.step_done("b", {}, 2.0)) == ["z"]


def test_ready_steps_with_the_longest_chain_behind_them_start_first() -> None:
    steps = (
        "  p:\n    action: x.y\n  q:\n    action: x.y\n"  # p and q are roots, declared p first
        "  r:\n    action: x.y\n    needs: [q]\n"
        "  s:\n    action: x.y\n    needs: [r]\n"
        "  z:\n    action: x.y\n    needs: [p, s]\n"
    )
    sch = make(steps, m=1)
    assert starts(sch.start(0.0)) == ["q"]  # q leads a chain of four, p a chain of two


def test_given_priorities_replace_the_longest_path() -> None:
    steps = (
        "  p:\n    action: x.y\n  q:\n    action: x.y\n  z:\n    action: x.y\n    needs: [p, q]\n"
    )
    plan = plan_of(doc(steps, "goals: {any_of: [z]}\n"))

    def first(priorities: dict[str, float]) -> list[str]:
        return starts(Scheduler(plan, {}, 1, priorities=priorities).start(0.0))

    assert first({"p": 1.0, "q": 5.0, "z": 1.0}) == ["q"]
    assert first({"p": 5.0, "q": 1.0, "z": 1.0}) == ["p"]
    # a step that is not named keeps its longest path (2: itself and z), so p ties and goes first
    assert first({"p": 2.0}) == ["p"]
    assert first({"p": 1.0}) == ["q"]


def test_a_simulation_takes_the_priorities_too() -> None:
    plan = plan_of(doc(LONG_TAIL, "goals: {all_of: [t, p, q]}\n"))
    # r opens the long tail: a priority that ranks it last gives the makespan of FIFO
    low = simulate(plan, {}, 2, durations=LONG_TAIL_TIMES, priorities={"r": 0.0, "t": 10.0})
    assert low.makespan == 12.0
    assert simulate(plan, {}, 2, durations=LONG_TAIL_TIMES).makespan == 11.0


def test_priority_uses_the_given_durations() -> None:
    steps = (
        "  p:\n    action: x.y\n  q:\n    action: x.y\n  z:\n    action: x.y\n    needs: [p, q]\n"
    )
    assert starts(make(steps, m=1, durations={"p": 1.0, "q": 5.0}).start(0.0)) == ["q"]
    assert starts(make(steps, m=1, durations={"p": 5.0, "q": 1.0}).start(0.0)) == ["p"]
    assert starts(make(steps, m=1).start(0.0)) == ["p"]  # equal weights: declaration order


def test_start_command_carries_the_action_and_the_rendered_arguments() -> None:
    plan = example_plan()
    sch = Scheduler(plan, {"quarter": "2025Q1"}, 9)
    first = sch.start(0.0)
    assert [c.step for c in first if isinstance(c, Start)] == [f"load[{r}]" for r in REGIONS]
    load_bis = first[0]
    assert isinstance(load_bis, Start)
    assert load_bis.action == "xlsx.read" and load_bis.args == {"path": "BIS/2025Q1.xlsx"}


def test_arguments_see_the_outputs_of_the_steps_before() -> None:
    sch = Scheduler(example_plan(), {"quarter": "2025Q1"}, 9)
    sch.start(0.0)
    commands = sch.step_done("load[BIS]", {"rows": [1, 2]}, 1.0)
    assert commands == [Start("check[BIS]", "statguard.validate", {"rows": [1, 2]})]


# -- US-07: skips, failures, quorum ----------------------------------------------------------------
def test_false_condition_skips_the_step_and_all_success_followers_but_not_all_done() -> None:
    steps = (
        "  b:\n    action: x.y\n    when: 1 > 2\n"
        "  c:\n    action: x.y\n    needs: [b]\n"
        "  d:\n    action: x.y\n    needs: [b]\n    join: all_done\n"
    )
    sch = make(steps, "goals: {any_of: [d]}\n")
    assert starts(sch.start(0.0)) == ["d"]
    assert (sch.states["b"], sch.states["c"]) == (S.SKIPPED, S.SKIPPED)
    sch.step_done("d", {}, 1.0)
    assert sch.outcome is RunOutcome.SUCCEEDED


def test_true_condition_runs_the_step() -> None:
    plan = plan_of(
        doc("  z:\n    action: x.y\n    when: ${inputs.n} > 2\n", inputs="  n: {type: int}\n")
    )
    assert starts(Scheduler(plan, {"n": 3}, 2).start(0.0)) == ["z"]
    assert Scheduler(plan, {"n": 1}, 2).start(0.0) == []


def failing(sch: Scheduler, *ids: str, now: float = 1.0) -> None:
    for step_id in ids:
        sch.step_failed(step_id, "E-ACT-001", "boom", now)


def test_quorum_with_two_failing_loads_runs_consolidate() -> None:
    sch = Scheduler(example_plan(), {"quarter": "2025Q1"}, 9)
    sch.start(0.0)
    failing(sch, "load[BIS]", "load[OSC]")
    for region in REGIONS[2:]:
        sch.step_done(f"load[{region}]", {"rows": [1]}, 1.0)
    # the checks of the two failed regions are UPSTREAM_FAILED, the other seven run
    assert sch.states["check[BIS]"] is S.UPSTREAM_FAILED
    for region in REGIONS[2:]:
        assert sch.states[f"check[{region}]"] is S.RUNNING
    commands = []
    for region in REGIONS[2:]:
        commands = sch.step_done(f"check[{region}]", {"rows": [1]}, 2.0)
    assert starts(commands) == ["consolidate"]
    consolidate = commands[0]
    assert isinstance(consolidate, Start)
    assert consolidate.args == {"parts": [[1]] * 7}  # only the instances that succeeded


def test_quorum_with_three_failing_loads_is_upstream_failed() -> None:
    sch = Scheduler(example_plan(), {"quarter": "2025Q1"}, 9)
    sch.start(0.0)
    failing(sch, "load[BIS]", "load[OSC]", "load[BAT]")
    for region in REGIONS[3:]:
        sch.step_done(f"load[{region}]", {"rows": [1]}, 1.0)
    for region in REGIONS[3:]:
        sch.step_done(f"check[{region}]", {"rows": [1]}, 2.0)
    assert sch.states["consolidate"] is S.UPSTREAM_FAILED
    assert sch.states["approve"] is S.UPSTREAM_FAILED
    assert sch.states["publish"] is S.UPSTREAM_FAILED
    assert sch.outcome is RunOutcome.GOAL_NOT_REACHED


def join_plan(join: str, k: int = 0) -> str:
    j = f"{{at_least: {k}}}" if join == "at_least" else join
    return (
        "  p:\n    action: x.y\n    on_failure: continue\n"
        "  q:\n    action: x.y\n    on_failure: continue\n"
        "  r:\n    action: x.y\n    on_failure: continue\n    when: 1 > 2\n"
        f"  z:\n    action: x.y\n    needs: [p, q, r]\n    join: {j}\n"
    )


@pytest.mark.parametrize(
    ("join", "p", "q", "want"),
    [
        ("all_success", "ok", "ok", S.SKIPPED),  # r was skipped
        ("all_success", "ok", "fail", S.UPSTREAM_FAILED),
        ("any_success", "ok", "fail", S.READY),
        ("any_success", "fail", "fail", S.UPSTREAM_FAILED),
        ("all_done", "fail", "fail", S.READY),
        ("at_least", "ok", "fail", S.UPSTREAM_FAILED),  # k = 2: one success, one failure
        ("at_least", "ok", "ok", S.READY),
    ],
)
def test_join_rules(join: str, p: str, q: str, want: StepState) -> None:
    k = 2
    sch = make(join_plan(join, k), m=4)
    sch.start(0.0)
    for name, result in (("p", p), ("q", q)):
        if result == "ok":
            sch.step_done(name, {}, 1.0)
        else:
            sch.step_failed(name, "E-ACT-001", "boom", 1.0)
    got = sch.states["z"]
    assert (got is S.RUNNING) if want is S.READY else (got is want)


def test_any_success_waits_until_every_predecessor_is_terminal() -> None:
    steps = (
        "  p:\n    action: x.y\n  q:\n    action: x.y\n"
        "  z:\n    action: x.y\n    needs: [p, q]\n    join: any_success\n"
    )
    sch = make(steps)
    sch.start(0.0)
    assert sch.step_done("p", {}, 1.0) == []  # results do not depend on timing: no early start
    assert starts(sch.step_done("q", {}, 2.0)) == ["z"]


def test_at_least_shortfall_from_skips_is_a_skip_not_a_failure() -> None:
    steps = (
        "  p:\n    action: x.y\n  q:\n    action: x.y\n    when: 1 > 2\n"
        "  r:\n    action: x.y\n    when: 1 > 2\n"
        "  z:\n    action: x.y\n    needs: [p, q, r]\n    join: {at_least: 2}\n"
    )
    sch = make(steps)
    sch.start(0.0)
    sch.step_done("p", {}, 1.0)
    assert sch.states["z"] is S.SKIPPED


# -- failure policy ------------------------------------------------------------------------------
def test_failure_with_the_default_policy_stops_the_run() -> None:
    steps = (
        A + B + "  c:\n    action: x.y\n    needs: [a]\n  z:\n    action: x.y\n    needs: [b, c]\n"
    )
    sch = make(steps, m=2)
    sch.start(0.0)
    commands = sch.step_failed("a", "E-ACT-001", "boom", 1.0)
    assert commands == [Cancel("b")]
    assert sch.states == {"a": S.FAILED, "b": S.CANCELLED, "c": S.CANCELLED, "z": S.CANCELLED}
    assert sch.outcome is RunOutcome.FAILED
    assert sch.errors["a"] == ("E-ACT-001", "boom")
    assert sch.step_done("b", {}, 2.0) == []  # a late result of a cancelled step is ignored
    assert sch.states["b"] is S.CANCELLED


def test_failure_with_continue_does_not_stop_the_run() -> None:
    steps = (
        "  a:\n    action: x.y\n    on_failure: continue\n"
        "  b:\n    action: x.y\n"
        "  z:\n    action: x.y\n    needs: [a, b]\n    join: all_done\n"
    )
    sch = make(steps, m=2)
    sch.start(0.0)
    assert sch.step_failed("a", "E-ACT-001", "boom", 1.0) == []
    assert sch.outcome is None
    assert starts(sch.step_done("b", {}, 2.0)) == ["z"]


def test_waiting_steps_are_cancelled_too() -> None:
    steps = (
        "  a:\n    action: x.y\n  w:\n    action: x.y\n  z:\n    action: x.y\n    needs: [a, w]\n"
    )
    sch = make(steps, m=2)
    sch.start(0.0)
    sch.step_waiting("w", 1.0)
    commands = sch.step_failed("a", "E-ACT-001", "boom", 2.0)
    assert commands == []  # a waiting step has nothing running to stop
    assert sch.states["w"] is S.CANCELLED and sch.outcome is RunOutcome.FAILED


def test_cancel_stops_everything() -> None:
    sch = make(DIAMOND, m=1)
    sch.start(0.0)
    commands = sch.cancel(1.0)
    assert commands == [Cancel("a")]
    assert set(sch.states.values()) == {S.CANCELLED}
    assert sch.outcome is RunOutcome.CANCELLED
    assert sch.cancel(2.0) == []


def test_cancel_before_start_and_after_the_end() -> None:
    sch = make(A, "goals: {any_of: [a]}\n")
    sch.start(0.0)
    sch.step_done("a", {}, 1.0)
    assert sch.cancel(2.0) == [] and sch.outcome is RunOutcome.SUCCEEDED


# -- US-08: waiting for an external answer ---------------------------------------------------------
APPROVAL = (
    "  prep:\n    action: x.y\n"
    "  approve:\n    action: approval.wait\n    needs: [prep]\n"
    "  publish:\n    action: x.y\n    needs: [approve]\n"
    "    when: ${approve.decision} == 'approved'\n"
    "  back:\n    action: x.y\n    needs: [approve]\n"
    "    when: ${approve.decision} == 'rejected'\n"
)
APPROVAL_GOALS = "goals: {any_of: [publish, back]}\n"


def waiting_run(m: int = 1) -> Scheduler:
    sch = make(APPROVAL, APPROVAL_GOALS, m=m)
    sch.start(0.0)
    assert starts(sch.step_done("prep", {}, 1.0)) == ["approve"]
    sch.step_waiting("approve", 2.0)
    return sch


def test_a_waiting_step_releases_its_slot() -> None:
    steps = (
        "  w:\n    action: approval.wait\n  other:\n    action: x.y\n"
        "  z:\n    action: x.y\n    needs: [w, other]\n"
    )
    sch = make(steps, m=1)
    assert starts(sch.start(0.0)) == ["w"]
    assert starts(sch.step_waiting("w", 1.0)) == ["other"]  # the slot is free again
    assert sch.states["w"] is S.WAITING and sch.status == "RUNNING"


def test_run_is_waiting_when_only_waiting_steps_are_left() -> None:
    sch = waiting_run()
    assert sch.states["approve"] is S.WAITING and sch.status == "WAITING"
    assert sch.outcome is None


def test_answer_approved_runs_the_approved_branch() -> None:
    sch = waiting_run()
    assert starts(sch.signal("approve", {"decision": "approved"}, 3.0)) == ["publish"]
    assert sch.states["approve"] is S.SUCCEEDED and sch.states["back"] is S.SKIPPED
    sch.step_done("publish", {}, 4.0)
    assert sch.outcome is RunOutcome.SUCCEEDED


def test_answer_rejected_is_data_and_runs_the_other_branch() -> None:
    sch = waiting_run()
    assert starts(sch.signal("approve", {"decision": "rejected"}, 3.0)) == ["back"]
    assert sch.states["approve"] is S.SUCCEEDED and sch.states["publish"] is S.SKIPPED
    assert sch.outputs["approve"] == {"decision": "rejected"}
    sch.step_done("back", {}, 4.0)
    assert sch.outcome is RunOutcome.SUCCEEDED  # US-09: a rejection with a handled branch succeeds


def test_a_signal_for_a_step_that_is_not_waiting_is_ignored() -> None:
    sch = waiting_run()
    assert sch.signal("prep", {"decision": "x"}, 3.0) == []
    assert sch.signal("publish", {"decision": "x"}, 3.0) == []
    sch.signal("approve", {"decision": "approved"}, 3.0)
    assert (
        sch.signal("approve", {"decision": "rejected"}, 4.0) == []
    )  # the second answer is ignored
    assert sch.outputs["approve"] == {"decision": "approved"}


def test_a_waiting_step_can_fail() -> None:
    sch = waiting_run()
    sch.step_failed("approve", "E-ACT-009", "timeout", 3.0)
    assert sch.states["approve"] is S.FAILED and sch.outcome is RunOutcome.FAILED


# -- US-09: outcome of the run -------------------------------------------------------------------
def test_no_goal_succeeded_is_goal_not_reached() -> None:
    sch = waiting_run()
    sch.signal("approve", {"decision": "maybe"}, 3.0)  # neither branch applies
    assert sch.states["publish"] is S.SKIPPED and sch.states["back"] is S.SKIPPED
    assert sch.outcome is RunOutcome.GOAL_NOT_REACHED


def test_all_of_needs_every_goal() -> None:
    steps = A + B
    sch = make(steps, "goals: {all_of: [a, b]}\n")
    sch.start(0.0)
    sch.step_done("a", {}, 1.0)
    sch.step_failed("b", "E-ACT-001", "x", 1.0)
    assert sch.outcome is RunOutcome.FAILED  # the default policy stopped the run
    sch2 = make(
        A + "  b:\n    action: x.y\n    on_failure: continue\n", "goals: {all_of: [a, b]}\n"
    )
    sch2.start(0.0)
    sch2.step_done("a", {}, 1.0)
    sch2.step_failed("b", "E-ACT-001", "x", 1.0)
    assert sch2.outcome is RunOutcome.GOAL_NOT_REACHED
    sch3 = make(A + B, "goals: {all_of: [a, b]}\n")
    sch3.start(0.0)
    sch3.step_done("a", {}, 1.0)
    sch3.step_done("b", {}, 1.0)
    assert sch3.outcome is RunOutcome.SUCCEEDED


# -- US-10: contract on the output -------------------------------------------------------------
CONTRACT = "  z:\n    action: x.y\n    ensures: ${z.written} > 0\n"


def test_contract_violation_fails_the_step() -> None:
    sch = make(CONTRACT)
    sch.start(0.0)
    sch.step_done("z", {"written": 0}, 1.0)
    assert sch.states["z"] is S.FAILED
    code, message = sch.errors["z"]
    assert code == "E-CONTRACT-001" and "${z.written}" in message
    assert "z" not in sch.outputs  # a step that broke its contract has no output
    assert sch.outcome is RunOutcome.FAILED


def test_contract_holds() -> None:
    sch = make(CONTRACT)
    sch.start(0.0)
    sch.step_done("z", {"written": 3}, 1.0)
    assert sch.states["z"] is S.SUCCEEDED and sch.outcome is RunOutcome.SUCCEEDED


def test_contract_with_a_missing_field_fails_with_the_evaluation_error() -> None:
    sch = make(CONTRACT)
    sch.start(0.0)
    sch.step_done("z", {"other": 1}, 1.0)
    assert sch.errors["z"][0] == "E-RUN-003"


def test_contract_that_is_not_a_boolean_fails() -> None:
    sch = make("  z:\n    action: x.y\n    ensures: ${z.written} + 1\n")
    sch.start(0.0)
    sch.step_done("z", {"written": 1}, 1.0)
    assert sch.errors["z"][0] == "E-RUN-008"


# -- runtime errors of expressions ---------------------------------------------------------------
def test_error_in_a_condition_fails_the_step() -> None:
    sch = make("  z:\n    action: x.y\n    when: 1 / 0 > 1\n")
    assert sch.start(0.0) == []
    assert sch.states["z"] is S.FAILED and sch.errors["z"][0] == "E-RUN-004"
    assert sch.outcome is RunOutcome.FAILED


def test_error_in_an_argument_fails_the_step_before_it_starts() -> None:
    sch = make(
        "  z:\n    action: x.y\n    with: {p: '${inputs.n}'}\n", declared="  n: {type: int}\n"
    )
    assert sch.start(0.0) == []
    assert sch.states["z"] is S.FAILED and sch.errors["z"][0] == "E-RUN-002"


def test_a_failure_before_start_does_not_use_a_slot() -> None:
    steps = (
        "  bad:\n    action: x.y\n    on_failure: continue\n    with: {p: '${inputs.n}'}\n"
        "  good:\n    action: x.y\n"
    )
    sch = make(steps, "goals: {any_of: [good]}\n", m=1, declared="  n: {type: int}\n")
    assert starts(sch.start(0.0)) == ["good"]
    assert sch.states["bad"] is S.FAILED


def test_exists_guards_the_output_of_a_failed_step() -> None:
    steps = (
        "  a:\n    action: x.y\n    on_failure: continue\n"
        "  z:\n    action: x.y\n    needs: [a]\n    join: all_done\n"
        "    when: exists(${a.rows})\n"
    )
    sch = make(steps)
    sch.start(0.0)
    sch.step_failed("a", "E-ACT-001", "boom", 1.0)
    assert sch.states["z"] is S.SKIPPED  # the guard answered false instead of failing


# -- events --------------------------------------------------------------------------------------
def test_events_tell_the_whole_story_with_the_given_times() -> None:
    sch = make(DIAMOND, m=2)
    sch.start(10.0)
    sch.step_done("a", {"k": 1}, 11.0)
    sch.step_done("b", {}, 12.0)
    sch.step_done("z", {}, 13.0)
    kinds = [(e.kind, e.step, e.at) for e in sch.events]
    assert kinds == [
        (EventKind.RUN_STARTED, None, 10.0),
        (EventKind.STEP_STARTED, "a", 10.0),
        (EventKind.STEP_STARTED, "b", 10.0),
        (EventKind.STEP_FINISHED, "a", 11.0),
        (EventKind.STEP_FINISHED, "b", 12.0),
        (EventKind.STEP_STARTED, "z", 12.0),
        (EventKind.STEP_FINISHED, "z", 13.0),
        (EventKind.RUN_FINISHED, None, 13.0),
    ]
    assert [e.seq for e in sch.events] == list(range(len(sch.events)))
    finished_a = sch.events[3]
    assert finished_a.state is S.SUCCEEDED and finished_a.output == {"k": 1}
    assert sch.events[-1].outcome is RunOutcome.SUCCEEDED


def test_events_record_failures_and_skips_with_the_reason() -> None:
    steps = (
        "  a:\n    action: x.y\n    on_failure: continue\n  z:\n    action: x.y\n    needs: [a]\n"
    )
    sch = make(steps, "goals: {any_of: [a]}\n")
    sch.start(0.0)
    sch.step_failed("a", "E-ACT-001", "disk full", 1.0)
    finished = [e for e in sch.events if e.kind is EventKind.STEP_FINISHED]
    assert (finished[0].step, finished[0].state, finished[0].code, finished[0].message) == (
        "a", S.FAILED, "E-ACT-001", "disk full",
    )  # fmt: skip
    assert (finished[1].step, finished[1].state) == ("z", S.UPSTREAM_FAILED)


def test_waiting_is_an_event() -> None:
    sch = waiting_run()
    assert [e.kind for e in sch.events if e.step == "approve"] == [
        EventKind.STEP_STARTED,
        EventKind.STEP_WAITING,
    ]


# -- misc ----------------------------------------------------------------------------------------
def test_unknown_or_repeated_events_are_ignored() -> None:
    sch = make(DIAMOND, m=2)
    sch.start(0.0)
    assert sch.step_done("a", {}, 1.0) == []
    assert sch.step_done("a", {"x": 1}, 2.0) == []  # twice
    assert sch.step_done("z", {}, 2.0) == []  # not running
    assert sch.step_failed("a", "E", "m", 3.0) == []  # already finished
    assert sch.step_waiting("a", 3.0) == []
    assert sch.states["a"] is S.SUCCEEDED and sch.outputs["a"] == {}


def test_the_run_cannot_start_twice() -> None:
    sch = make(DIAMOND, m=2)
    assert starts(sch.start(0.0)) == ["a", "b"]
    assert sch.start(1.0) == []


def test_at_least_one_slot_is_needed() -> None:
    with pytest.raises(ValueError, match="max_parallel"):
        make(DIAMOND, m=0)


def test_a_long_failure_cascade_does_not_recurse() -> None:
    n = 3000
    steps = "  s0:\n    action: x.y\n    on_failure: continue\n"
    for i in range(1, n):
        steps += f"  s{i}:\n    action: x.y\n    needs: [s{i - 1}]\n"
    sch = Scheduler(plan_of(doc(steps, f"goals: {{any_of: [s{n - 1}]}}\n")), {}, 1)
    sch.start(0.0)
    sch.step_failed("s0", "E", "boom", 1.0)
    assert sch.states[f"s{n - 1}"] is S.UPSTREAM_FAILED
    assert sch.outcome is RunOutcome.GOAL_NOT_REACHED


# -- queue policy: plain longest path against FIFO (benchmark baseline) --------------------------
LONG_TAIL = (
    "  p:\n    action: x.y\n  q:\n    action: x.y\n  r:\n    action: x.y\n"
    "  t:\n    action: x.y\n    needs: [r]\n"
)
LONG_TAIL_TIMES = {"p": 1.0, "q": 1.0, "r": 1.0, "t": 10.0}


def test_longest_path_beats_fifo_when_a_short_step_opens_a_long_tail() -> None:
    plan = plan_of(doc(LONG_TAIL, "goals: {all_of: [t, p, q]}\n"))
    best = simulate(plan, {}, 2, durations=LONG_TAIL_TIMES)
    fifo = simulate(plan, {}, 2, durations=LONG_TAIL_TIMES, policy="fifo")
    assert best.makespan == 11.0  # r starts at once, so t ends at 1 + 10
    assert fifo.makespan == 12.0  # r waits behind p and q, so t ends at 2 + 10
    assert best.started["r"] == 0.0 and fifo.started["r"] == 1.0


def test_fifo_starts_steps_in_the_order_they_became_ready() -> None:
    steps = (
        "  a:\n    action: x.y\n  b:\n    action: x.y\n"
        "  late:\n    action: x.y\n    needs: [a]\n  early:\n    action: x.y\n"
    )
    plan = plan_of(doc(steps, "goals: {all_of: [late, early, b]}\n"))
    sim = simulate(plan, {}, 1, policy="fifo")
    order = [e.step for e in sim.scheduler.events if e.kind is EventKind.STEP_STARTED]
    assert order == ["a", "b", "early", "late"]


def test_the_policy_changes_the_order_but_never_the_result() -> None:
    inputs = {"quarter": "2025Q1"}

    def act(command: Start) -> Result:
        if command.step == "load[BIS]":
            return Fail(1.0, "E-ACT-002", "cannot read BIS/2025Q1.xlsx")
        return Done(1.0, {"rows": [1, 2], "written": 2, "decision": "approved"})

    one = simulate(example_plan(), inputs, 3, act)
    two = simulate(example_plan(), inputs, 3, act, policy="fifo")
    assert one.scheduler.states == two.scheduler.states
    assert one.scheduler.outcome == two.scheduler.outcome


def test_an_unknown_policy_is_refused() -> None:
    with pytest.raises(ValueError, match="policy"):
        Scheduler(
            plan_of(doc("  a:\n    action: x.y\n", "goals: {any_of: [a]}\n")), {}, 1, policy="lifo"
        )
