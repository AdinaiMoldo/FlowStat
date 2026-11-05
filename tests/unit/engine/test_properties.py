"""Property tests of the scheduler on random workflows (the invariants of the Proposal, C3).

A random workflow is generated as a specification, written as YAML, compiled by the real compiler
and run on the simulated clock. An independent oracle computes the expected final state of every
step from the specification alone. The same workflow is run with different seeds, different
durations and different numbers of workers: the results must not depend on timing.
"""

from __future__ import annotations

import itertools
import random
import zlib
from dataclasses import dataclass

import pytest

from flowstat.engine.scheduler import Start
from flowstat.engine.simulation import Done, Fail, Result, simulate
from flowstat.model.events import EventKind
from flowstat.model.states import TERMINAL_STEP_STATES, RunOutcome, StepState
from tests.unit.engine.helpers import plan_of

S = StepState
INPUTS = {"x": 4, "y": 2}
DECLARED = "  x: {type: int}\n  y: {type: int}\n"


@dataclass(frozen=True)
class StepSpec:
    name: str
    needs: tuple[str, ...]
    join: str  # all_success, any_success, all_done or at_least
    k: int
    when: tuple[object, ...]  # ("none",) ("cmp", c) ("sum", c) ("exists", dep, c)
    policy: str  # continue or fail


@dataclass(frozen=True)
class WorkflowSpec:
    steps: tuple[StepSpec, ...]
    goal_kind: str
    goals: tuple[str, ...]
    case: int  # decides which steps fail


def gen_spec(rng: random.Random, policy: str) -> WorkflowSpec:
    n = rng.randint(2, 11)
    steps: list[StepSpec] = []
    for i in range(n):
        earlier = [s.name for s in steps]
        count = rng.randint(0, min(3, len(earlier)))
        needs = tuple(sorted(rng.sample(earlier, count), key=earlier.index))
        join = rng.choice(["all_success", "all_success", "any_success", "all_done", "at_least"])
        if not needs:
            join = "all_success"
        k = rng.randint(1, max(1, len(needs))) if join == "at_least" else 0
        if join == "at_least" and not needs:
            join = "all_success"
        kind = rng.random()
        when: tuple[object, ...] = ("none",)
        if kind < 0.2:
            when = ("cmp", rng.randint(0, 6))
        elif kind < 0.3:
            when = ("sum", rng.choice([6, 5, 7]))
        elif kind < 0.5 and needs:
            when = ("exists", rng.choice(needs), rng.randint(0, 6))
        steps.append(StepSpec(f"s{i}", needs, join, k, when, policy))
    names = [s.name for s in steps]
    goals = tuple(rng.sample(names, rng.randint(1, min(3, n))))
    return WorkflowSpec(
        tuple(steps), rng.choice(["any_of", "all_of"]), goals, rng.randint(0, 10**6)
    )


def to_yaml(spec: WorkflowSpec) -> str:
    out = "workflow: random\nversion: 1\ninputs:\n" + DECLARED + "steps:\n"
    for s in spec.steps:
        out += f"  {s.name}:\n    action: x.y\n    on_failure: {s.policy}\n"
        if s.needs:
            out += f"    needs: [{', '.join(s.needs)}]\n"
        if s.join == "at_least":
            out += f"    join: {{at_least: {s.k}}}\n"
        elif s.join != "all_success":
            out += f"    join: {s.join}\n"
        match s.when:
            case ("cmp", c):
                out += f"    when: ${{inputs.x}} > {c}\n"
            case ("sum", c):
                out += f"    when: ${{inputs.x}} + ${{inputs.y}} == {c}\n"
            case ("exists", dep, c):
                out += f"    when: exists(${{{dep}.v}}) and ${{{dep}.v}} > {c}\n"
    out += f"goals: {{{spec.goal_kind}: [{', '.join(spec.goals)}]}}\n"
    return out


def fails(spec: WorkflowSpec, step: str) -> bool:
    return zlib.crc32(f"{spec.case}/{step}".encode()) % 4 == 0


def value_of(spec: WorkflowSpec, step: str) -> int:
    return zlib.crc32(f"v{spec.case}/{step}".encode()) % 7


def behavior_of(spec: WorkflowSpec, rng: random.Random) -> object:
    def act(command: Start) -> Result:
        duration = float(rng.randint(1, 5))
        if fails(spec, command.step):
            return Fail(duration, "E-ACT-001", "boom")
        return Done(duration, {"v": value_of(spec, command.step)})

    return act


# -- the oracle ----------------------------------------------------------------------------------
def oracle_join(spec: StepSpec, states: list[StepState]) -> StepState | None:
    if not states or spec.join == "all_done":
        return None
    ok = [s for s in states if s is S.SUCCEEDED]
    bad = [s for s in states if s in (S.FAILED, S.UPSTREAM_FAILED)]
    if spec.join == "all_success":
        if len(ok) == len(states):
            return None
        return S.UPSTREAM_FAILED if bad else S.SKIPPED
    if spec.join == "any_success":
        if ok:
            return None
        return S.UPSTREAM_FAILED if bad else S.SKIPPED
    if len(ok) >= spec.k:
        return None
    return S.UPSTREAM_FAILED if bad and len(ok) + len(bad) >= spec.k else S.SKIPPED


def oracle(spec: WorkflowSpec) -> tuple[dict[str, StepState], RunOutcome]:
    """Final states when every failure has the continue policy (the timing does not matter)."""
    states: dict[str, StepState] = {}
    for s in spec.steps:
        verdict = oracle_join(s, [states[n] for n in s.needs])
        if verdict is not None:
            states[s.name] = verdict
            continue
        match s.when:
            case ("cmp", c):
                holds = INPUTS["x"] > int(str(c))
            case ("sum", c):
                holds = INPUTS["x"] + INPUTS["y"] == int(str(c))
            case ("exists", dep, c):
                holds = states[str(dep)] is S.SUCCEEDED and value_of(spec, str(dep)) > int(str(c))
            case _:
                holds = True
        if not holds:
            states[s.name] = S.SKIPPED
        else:
            states[s.name] = S.FAILED if fails(spec, s.name) else S.SUCCEEDED
    reached = [states[g] is S.SUCCEEDED for g in spec.goals]
    ok = all(reached) if spec.goal_kind == "all_of" else any(reached)
    return states, RunOutcome.SUCCEEDED if ok else RunOutcome.GOAL_NOT_REACHED


def run(spec: WorkflowSpec, m: int, seed: int):  # type: ignore[no-untyped-def]
    plan = plan_of(to_yaml(spec))
    rng = random.Random(seed * 7919 + 1)
    return simulate(plan, INPUTS, m, behavior_of(spec, rng), seed=seed)  # type: ignore[arg-type]


CASES = 70


@pytest.mark.parametrize("m", [1, 2, 3, 100])
def test_final_states_and_outcome_match_the_oracle(m: int) -> None:
    rng = random.Random(1000 + m)
    for case in range(CASES):
        spec = gen_spec(rng, "continue")
        sim = run(spec, m, seed=case)
        want_states, want_outcome = oracle(spec)
        assert sim.scheduler.states == want_states, (case, to_yaml(spec))
        assert sim.scheduler.outcome is want_outcome, (case, to_yaml(spec))


def test_results_do_not_depend_on_timing_or_on_the_number_of_workers() -> None:
    rng = random.Random(55)
    for _ in range(CASES):
        spec = gen_spec(rng, "continue")
        runs = [run(spec, m, seed) for m, seed in ((1, 1), (2, 2), (3, 3), (50, 4), (2, 5))]
        first = runs[0].scheduler
        for other in runs[1:]:
            assert other.scheduler.states == first.states, to_yaml(spec)
            assert other.scheduler.outputs == first.outputs
            assert other.scheduler.errors == first.errors
            assert other.scheduler.outcome is first.outcome


def test_invariants_of_every_run() -> None:
    rng = random.Random(77)
    for case in range(CASES):
        policy = rng.choice(["continue", "fail"])
        spec = gen_spec(rng, policy)
        m = rng.randint(1, 4)
        sim = run(spec, m, seed=case)
        sch = sim.scheduler
        plan = sch.plan
        # every step is terminal and the run has an outcome
        assert all(state in TERMINAL_STEP_STATES for state in sch.states.values()), to_yaml(spec)
        assert sch.outcome is not None
        # no step starts before its predecessors are terminal; at most m run at once
        finished_at: dict[str, int] = {}
        active: set[str] = set()
        for event in sch.events:
            step_id = event.step or ""
            if event.kind is EventKind.STEP_FINISHED:
                finished_at[step_id] = event.seq
                active.discard(step_id)
            if event.kind is EventKind.STEP_STARTED:
                for need in plan.step(step_id).needs:
                    assert need in finished_at and finished_at[need] < event.seq, to_yaml(spec)
                active.add(step_id)
                assert len(active) <= m, to_yaml(spec)
        # a step that never started has no output; a succeeded step has one
        for step_id, state in sch.states.items():
            assert (step_id in sch.outputs) == (state is S.SUCCEEDED)
            assert (step_id in sch.errors) == (state is S.FAILED)
        # the events are numbered without gaps and the run finishes exactly once
        assert [e.seq for e in sch.events] == list(range(len(sch.events)))
        assert [e.kind for e in sch.events].count(EventKind.RUN_FINISHED) == 1
        if policy == "fail" and any(s is S.FAILED for s in sch.states.values()):
            assert sch.outcome is RunOutcome.FAILED


def test_the_same_inputs_and_seed_give_the_same_history() -> None:
    rng = random.Random(3)
    for case in range(60):
        spec = gen_spec(rng, rng.choice(["continue", "fail"]))
        a = run(spec, 2, seed=case)
        b = run(spec, 2, seed=case)
        assert a.scheduler.events == b.scheduler.events
        assert a.makespan == b.makespan


def test_with_enough_workers_the_makespan_is_the_longest_chain() -> None:
    rng = random.Random(11)
    for _ in range(80):
        spec = gen_spec(rng, "continue")
        plain = WorkflowSpec(
            tuple(
                StepSpec(s.name, s.needs, "all_done", 0, ("none",), "continue") for s in spec.steps
            ),
            spec.goal_kind,
            spec.goals,
            spec.case,
        )
        plan = plan_of(to_yaml(plain))
        sim = simulate(plan, INPUTS, 100)
        longest: dict[str, int] = {}
        for s in plain.steps:
            longest[s.name] = 1 + max((longest[n] for n in s.needs), default=0)
        assert sim.makespan == max(longest.values()), to_yaml(plain)


def test_one_worker_runs_one_step_at_a_time() -> None:
    rng = random.Random(21)
    for case in range(60):
        spec = gen_spec(rng, "continue")
        sim = run(spec, 1, seed=case)
        spans = sorted((sim.started[s], sim.finished[s]) for s in sim.started if s in sim.finished)
        for (_, end), (start, _) in itertools.pairwise(spans):
            assert end <= start
