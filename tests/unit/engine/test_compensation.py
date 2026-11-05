"""Tests of compensation (US-14): undo completed steps in reverse dependency order."""

from __future__ import annotations

import random
from collections.abc import Mapping

import pytest

from flowstat.compiler.catalogue import Catalogue
from flowstat.compiler.compile import compile_workflow
from flowstat.compiler.plan import Plan
from flowstat.engine.scheduler import Scheduler, Start
from flowstat.engine.simulation import Done, Fail, Result, Simulation, simulate
from flowstat.model.events import EventKind
from flowstat.model.states import RunOutcome, StepState
from flowstat.recovery.saga import compensation_blockers
from tests.unit.engine.helpers import doc, plan_of

S = StepState

CHAIN = (
    "  a:\n    action: x.y\n    compensate: x.undo_a\n"
    "  b:\n    action: x.y\n    needs: [a]\n    compensate: x.undo_b\n"
    "  c:\n    action: x.y\n    needs: [b]\n    on_failure: compensate\n"
)


def chain_plan(steps: str = CHAIN) -> Plan:
    return plan_of(doc(steps, "goals: {any_of: [c]}\n"))


def run(
    plan: Plan,
    behavior: object,
    workers: int = 2,
    inputs: Mapping[str, object] | None = None,
    seed: int = 0,
) -> Simulation:
    return simulate(
        plan,
        inputs or {},
        workers,
        behavior,  # type: ignore[arg-type]
        seed=seed,
        compensation_planner=compensation_blockers,
    )


def c_fails(times: Mapping[str, float] | None = None):  # type: ignore[no-untyped-def]
    """Every step succeeds, step c fails, an undo takes the time given for the step."""
    times = times or {}

    def act(command: Start) -> Result:
        if command.compensation:
            return Done(times.get(command.step, 1.0), {})
        if command.step == "c":
            return Fail(1.0, "E-ACT-002", "c broke")
        return Done(1.0, {"n": 1})

    return act


# -- the story ----------------------------------------------------------------------------------
def test_b_is_undone_before_a_and_the_run_is_compensated() -> None:
    sim = run(chain_plan(), c_fails({"a": 3.0, "b": 2.0}))
    assert sim.scheduler.outcome is RunOutcome.COMPENSATED
    assert sim.scheduler.states == {"a": S.COMPENSATED, "b": S.COMPENSATED, "c": S.FAILED}
    (b_start, b_end), (a_start, _) = sim.undone["b"], sim.undone["a"]
    assert b_end <= a_start  # b finishes before a starts, whatever the durations
    assert b_start < b_end


def test_the_order_does_not_depend_on_the_number_of_workers() -> None:
    for workers in (1, 2, 100):
        sim = run(chain_plan(), c_fails({"a": 1.0, "b": 5.0}), workers)
        assert sim.undone["b"][1] <= sim.undone["a"][0]


def test_the_events_tell_the_story_in_order() -> None:
    sim = run(chain_plan(), c_fails(), 1)
    kinds = [(e.kind, e.step) for e in sim.scheduler.events[-7:]]
    assert kinds == [
        (EventKind.STEP_FINISHED, "c"),
        (EventKind.COMPENSATION_STARTED, None),
        (EventKind.STEP_COMPENSATING, "b"),
        (EventKind.STEP_FINISHED, "b"),
        (EventKind.STEP_COMPENSATING, "a"),
        (EventKind.STEP_FINISHED, "a"),
        (EventKind.RUN_FINISHED, None),
    ]
    assert [e.state for e in sim.scheduler.events if e.step == "b"][-1] is S.COMPENSATED


def test_the_compensation_gets_the_arguments_of_the_original_call() -> None:
    steps = (
        "  a:\n    action: x.y\n    with: {p: '${inputs.q}'}\n    compensate: x.undo_a\n"
        "  c:\n    action: x.y\n    needs: [a]\n    on_failure: compensate\n"
    )
    plan = plan_of(doc(steps, "goals: {any_of: [c]}\n", inputs="  q: {type: string}\n"))
    seen: list[Start] = []

    def act(command: Start) -> Result:
        seen.append(command)
        return Fail(1.0, "E-ACT-002", "x") if command.step == "c" else Done(1.0, {})

    run(plan, act, inputs={"q": "v"})
    undo = next(c for c in seen if c.compensation)
    assert (undo.step, undo.action, undo.args, undo.attempt) == ("a", "x.undo_a", {"p": "v"}, 1)


def test_a_step_without_compensation_stays_succeeded_but_still_orders_the_others() -> None:
    steps = (
        "  a:\n    action: x.y\n    compensate: x.undo_a\n"
        "  m:\n    action: x.y\n    needs: [a]\n"
        "  b:\n    action: x.y\n    needs: [m]\n    compensate: x.undo_b\n"
        "  c:\n    action: x.y\n    needs: [b]\n    on_failure: compensate\n"
    )
    sim = run(chain_plan(steps), c_fails({"a": 1.0, "b": 4.0}))
    assert sim.scheduler.states["m"] is S.SUCCEEDED
    assert sim.undone["b"][1] <= sim.undone["a"][0]


def test_independent_compensations_run_in_parallel_within_the_limit() -> None:
    steps = (
        "  a:\n    action: x.y\n    compensate: x.undo\n"
        "  b:\n    action: x.y\n    compensate: x.undo\n"
        "  c:\n    action: x.y\n    needs: [a, b]\n    on_failure: compensate\n"
    )
    two = run(chain_plan(steps), c_fails({"a": 3.0, "b": 3.0}), workers=2)
    assert two.undone["a"][0] == two.undone["b"][0]  # they overlap
    one = run(chain_plan(steps), c_fails({"a": 3.0, "b": 3.0}), workers=1)
    assert one.undone["a"][1] <= one.undone["b"][0] or one.undone["b"][1] <= one.undone["a"][0]


def test_steps_that_were_running_are_cancelled_and_not_undone() -> None:
    steps = (
        "  a:\n    action: x.y\n    compensate: x.undo_a\n"
        "  slow:\n    action: x.y\n    compensate: x.undo_slow\n"
        "  c:\n    action: x.y\n    needs: [a]\n    on_failure: compensate\n"
    )

    def act(command: Start) -> Result:
        if command.compensation:
            return Done(1.0, {})
        if command.step == "slow":
            return Done(100.0, {})  # still running when c fails
        return Fail(1.0, "E-ACT-002", "c broke") if command.step == "c" else Done(1.0, {})

    sim = run(chain_plan(steps), act, workers=3)
    assert sim.scheduler.states["slow"] is S.CANCELLED and "slow" not in sim.undone
    assert sim.scheduler.states["a"] is S.COMPENSATED
    assert sim.scheduler.outcome is RunOutcome.COMPENSATED


def test_a_run_with_nothing_to_undo_is_compensated_at_once() -> None:
    steps = "  c:\n    action: x.y\n    on_failure: compensate\n"
    sim = run(chain_plan(steps), c_fails())
    assert sim.scheduler.outcome is RunOutcome.COMPENSATED and sim.undone == {}


def test_a_failure_with_the_default_policy_does_not_compensate() -> None:
    sim = run(chain_plan(CHAIN.replace("    on_failure: compensate\n", "")), c_fails())
    assert sim.scheduler.outcome is RunOutcome.FAILED
    assert sim.scheduler.states["a"] is S.SUCCEEDED and sim.undone == {}


def test_a_failure_that_continues_does_not_compensate() -> None:
    steps = CHAIN.replace("on_failure: compensate", "on_failure: continue")
    sim = run(chain_plan(steps), c_fails())
    assert sim.scheduler.outcome is not RunOutcome.COMPENSATED and sim.undone == {}


def test_the_run_is_compensating_until_the_last_undo_is_done() -> None:
    s = Scheduler(chain_plan(), {}, 1, compensation_planner=compensation_blockers)
    s.start(0.0)
    s.step_done("a", {}, 1.0)
    s.step_done("b", {}, 2.0)
    commands = s.step_failed("c", "E-ACT-002", "x", 3.0)
    assert [(c.step, c.action) for c in commands if isinstance(c, Start)] == [("b", "x.undo_b")]
    assert s.status == "COMPENSATING" and s.outcome is None
    s.step_done("b", {}, 4.0)
    assert s.states["b"] is S.COMPENSATED and s.status == "COMPENSATING"
    s.step_done("a", {}, 5.0)
    assert s.status == "FINISHED" and s.outcome is RunOutcome.COMPENSATED


def test_a_planner_is_required_when_a_step_uses_the_compensate_policy() -> None:
    with pytest.raises(ValueError, match="compensat"):
        Scheduler(chain_plan(), {}, 1)


def test_cancel_during_compensation_is_ignored() -> None:
    s = Scheduler(chain_plan(), {}, 1, compensation_planner=compensation_blockers)
    s.start(0.0)
    s.step_done("a", {}, 1.0)
    s.step_done("b", {}, 2.0)
    s.step_failed("c", "E-ACT-002", "x", 3.0)
    assert s.cancel(4.0) == [] and s.status == "COMPENSATING"


# -- when an undo fails -----------------------------------------------------------------------
def undo_fails(failing: set[str], transient: bool = False, times: int = 99):  # type: ignore[no-untyped-def]
    seen: dict[str, int] = {}

    def act(command: Start) -> Result:
        if command.compensation:
            seen[command.step] = seen.get(command.step, 0) + 1
            if command.step in failing and seen[command.step] <= times:
                return Fail(1.0, "E-ACT-008", "undo lost", transient=transient)
            return Done(1.0, {})
        return Fail(1.0, "E-ACT-002", "c broke") if command.step == "c" else Done(1.0, {})

    return act


def test_a_failed_undo_blocks_the_steps_waiting_for_it_and_the_run_says_so() -> None:
    sim = run(chain_plan(), undo_fails({"b"}))
    states = sim.scheduler.states
    assert (states["b"], states["a"]) == (S.COMPENSATION_FAILED, S.SUCCEEDED)  # a was never undone
    assert sim.scheduler.outcome is RunOutcome.COMPENSATION_FAILED
    code, message = sim.scheduler.errors["b"]
    assert code == "E-COMP-001" and "E-ACT-008" in message and "undo lost" in message


def test_an_undo_that_does_not_depend_on_the_failed_one_still_runs() -> None:
    steps = (
        "  a:\n    action: x.y\n    compensate: x.undo\n"
        "  b:\n    action: x.y\n    compensate: x.undo\n"
        "  c:\n    action: x.y\n    needs: [a, b]\n    on_failure: compensate\n"
    )
    sim = run(chain_plan(steps), undo_fails({"a"}))
    assert sim.scheduler.states["a"] is S.COMPENSATION_FAILED
    assert sim.scheduler.states["b"] is S.COMPENSATED
    assert sim.scheduler.outcome is RunOutcome.COMPENSATION_FAILED


def test_a_transient_undo_failure_is_retried_with_three_exponential_retries_by_default() -> None:
    sim = run(chain_plan(), undo_fails({"b"}, transient=True, times=2))
    assert sim.scheduler.outcome is RunOutcome.COMPENSATED
    delays = [e.delay for e in sim.scheduler.events if e.kind is EventKind.STEP_RETRY]
    assert delays == [1.0, 2.0]


def test_the_undo_retries_run_out() -> None:
    sim = run(chain_plan(), undo_fails({"b"}, transient=True))
    assert sim.scheduler.outcome is RunOutcome.COMPENSATION_FAILED
    delays = [e.delay for e in sim.scheduler.events if e.kind is EventKind.STEP_RETRY]
    assert delays == [1.0, 2.0, 4.0]  # three retries: four attempts in all


def test_the_retry_of_the_step_is_used_for_its_undo_when_it_has_one() -> None:
    steps = CHAIN.replace(
        "    compensate: x.undo_b\n",
        "    compensate: x.undo_b\n    retry: {max: 1, backoff: fixed, delay: 0.5}\n",
    )
    sim = run(chain_plan(steps), undo_fails({"b"}, transient=True))
    delays = [e.delay for e in sim.scheduler.events if e.kind is EventKind.STEP_RETRY]
    assert delays == [0.5]


# -- random workflows: order and completeness -----------------------------------------------------
def random_saga(rng: random.Random, steps: int) -> tuple[Plan, str]:
    failing = rng.randrange(1, steps)
    lines = ["workflow: bench", "version: 1", "steps:"]
    for i in range(steps):
        lines += [f"  s{i}:", "    action: x.y"]
        window = list(range(max(0, i - 6), i))
        picked = sorted(rng.sample(window, min(len(window), rng.choice([0, 1, 1, 2]))))
        if picked:
            lines.append("    needs: [" + ", ".join(f"s{j}" for j in picked) + "]")
        if rng.random() < 0.6:
            lines.append("    compensate: x.undo")
        if i == failing:
            lines.append("    on_failure: compensate")
    lines.append(f"goals: {{any_of: [s{steps - 1}]}}")
    result = compile_workflow("\n".join(lines) + "\n", Catalogue({}))
    assert result.plan is not None
    return result.plan, f"s{failing}"


def descendants(plan: Plan, step: str) -> set[str]:
    followers: dict[str, list[str]] = {s.id: [] for s in plan.steps}
    for s in plan.steps:
        for p in s.needs:
            followers[p].append(s.id)
    seen: set[str] = set()
    stack = list(followers[step])
    while stack:
        x = stack.pop()
        if x not in seen:
            seen.add(x)
            stack.extend(followers[x])
    return seen


@pytest.mark.property
@pytest.mark.parametrize("seed", range(40))
def test_random_sagas_undo_exactly_what_succeeded_in_reverse_order(seed: int) -> None:
    rng = random.Random(seed)
    plan, failing = random_saga(rng, rng.choice([6, 15, 30]))
    undo_time = {s.id: 1.0 + rng.random() * 3 for s in plan.steps}

    def act(command: Start) -> Result:
        if command.compensation:
            return Done(undo_time[command.step], {})
        return (
            Fail(1.0, "E-ACT-002", "x") if command.step == failing else Done(1.0 + rng.random(), {})
        )

    for workers in (1, 3, 100):
        sim = run(plan, act, workers, seed=seed)
        s = sim.scheduler
        assert s.outcome is RunOutcome.COMPENSATED
        started = [e for e in s.events if e.kind is EventKind.COMPENSATION_STARTED]
        assert len(started) == 1
        before = {  # the state of every step when the compensation began
            e.step: e.state
            for e in s.events[: s.events.index(started[0])]
            if e.kind is EventKind.STEP_FINISHED and e.step is not None
        }
        to_undo = {
            x.id for x in plan.steps if before.get(x.id) is S.SUCCEEDED and x.compensate is not None
        }
        assert {k for k, v in s.states.items() if v is S.COMPENSATED} == to_undo
        for u in to_undo:
            for v in descendants(plan, u) & to_undo:
                assert sim.undone[v][1] <= sim.undone[u][0], (u, v)
        points = sorted(t for pair in sim.undone.values() for t in pair)
        for t in points:  # at most `workers` undos run at any moment
            active = sum(1 for a, b in sim.undone.values() if a <= t < b)
            assert active <= workers
    # Which steps had succeeded when the failure stopped the run depends on how many ran at once,
    # so the final states may differ between worker counts; each run undoes exactly its own.
