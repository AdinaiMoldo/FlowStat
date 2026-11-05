"""Tests of the feasible critical path (C3, US-35): docs/dsl-spec.md, section 11.

The oracle is the reference interpreter (``tests/reference``): with every input known and nothing
failing, the steps that run are known, and the priority of a step is the longest chain of steps
that ran, from it onwards. With the inputs unknown the priority is an upper bound of that length
for every input, and the plain longest path is an upper bound of the priority.
"""

from __future__ import annotations

import random

import pytest

from flowstat.analysis.critical import CAP, priorities
from flowstat.compiler.plan import Plan
from tests.reference.interp import SUCCEEDED, ancestors, longest_chain, run
from tests.unit.analysis.helpers import SIGNATURES, compile_plan, random_workflow, step


def plain(plan: Plan, durations: dict[str, float]) -> dict[str, float]:
    """The longest path of the scheduler of M2: duration plus the longest chain behind it."""
    out: dict[str, float] = {}
    successors: dict[str, list[str]] = {s.id: [] for s in plan.steps}
    for s in plan.steps:
        for p in s.needs:
            successors[p].append(s.id)
    for s in reversed(plan.steps):
        tail = max((out[j] for j in successors[s.id]), default=0.0)
        out[s.id] = durations.get(s.id, 1.0) + tail
    return out


def tails(plan: Plan, ran: set[str], durations: dict[str, float]) -> dict[str, float]:
    """The longest chain of steps of ``ran`` that starts at each of them (reference reading)."""
    above = ancestors(plan)
    out: dict[str, float] = {}
    for s in reversed(plan.steps):
        if s.id in ran:
            later = [out[d.id] for d in plan.steps if d.id in ran and s.id in above[d.id]]
            out[s.id] = durations.get(s.id, 1.0) + max(later, default=0.0)
    return out


def values_of(rng: random.Random) -> dict[str, object]:
    return {
        "inputs.amount": rng.choice([0, 25, 26, 50, 51, 75, 100]),
        "inputs.mode": rng.choice(["fast", "slow", "off"]),
        "inputs.ratio": rng.choice([0.0, 0.25, 0.5, 0.75, 1.0]),
        "inputs.flag": rng.random() < 0.5,
    }


def given(values: dict[str, object]) -> dict[str, object]:
    return {k.removeprefix("inputs."): v for k, v in values.items()}


def random_durations(plan: Plan, rng: random.Random) -> dict[str, float]:
    return {s.id: float(rng.randint(1, 9)) for s in plan.steps}


# -- the oracle -------------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(4))
def test_with_every_input_known_the_priority_is_the_longest_chain_that_runs(seed: int) -> None:
    rng = random.Random(1300 + seed)
    compared = shorter = 0
    for _ in range(150):
        plan = random_workflow(rng, guard_chance=0.7)
        durations = random_durations(plan, rng)
        base = plain(plan, durations)
        for _ in range(4):
            values = values_of(rng)
            ran = {s for s, v in run(plan, values).items() if v == SUCCEEDED}
            got = priorities(plan, SIGNATURES, durations, given(values))
            assert not got.cut
            for s, expected in tails(plan, ran, durations).items():
                assert got.priority[s] == expected, (plan.name, s, values)
                compared += 1
                shorter += expected < base[s]
            if ran:  # the longest chain of the run is the largest priority among the steps that ran
                assert max(got.priority[s] for s in ran) == longest_chain(plan, ran, durations)
    assert compared > 1000 and shorter > 150  # the feasible path is not the plain one


@pytest.mark.parametrize("seed", range(4))
def test_with_unknown_inputs_the_priority_bounds_every_run_and_the_plain_path(seed: int) -> None:
    rng = random.Random(1700 + seed)
    below = checked = 0
    for _ in range(150):
        plan = random_workflow(rng, guard_chance=0.7)
        durations = random_durations(plan, rng)
        base = plain(plan, durations)
        got = priorities(plan, SIGNATURES, durations)
        for s in plan.steps:
            assert got.priority[s.id] <= base[s.id]
            below += got.priority[s.id] < base[s.id]
        for _ in range(6):
            values = values_of(rng)
            ran = {s for s, v in run(plan, values).items() if v == SUCCEEDED}
            for name, length in tails(plan, ran, durations).items():
                assert length <= got.priority[name], (plan.name, name, values)
                checked += 1
    assert checked > 1500 and below > 40


# -- hand-made cases --------------------------------------------------------------------------
PROPOSAL = (
    step("A")
    + step("B", "${inputs.amount} > 5", ["A"])
    + step("C", "${inputs.amount} < 3", ["B"], "all_done")
)
LENGTHS = {"A": 1.0, "B": 10.0, "C": 10.0}


def test_the_example_of_the_proposal_is_11_and_not_21() -> None:
    plan = compile_plan(PROPOSAL, "C")
    assert plain(plan, LENGTHS)["A"] == 21.0
    assert priorities(plan, SIGNATURES, LENGTHS).priority["A"] == 11.0
    # the guards are decided by the inputs of the run
    assert priorities(plan, SIGNATURES, LENGTHS, {"amount": 9}).priority["A"] == 11.0  # A, B
    assert priorities(plan, SIGNATURES, LENGTHS, {"amount": 1}).priority["A"] == 11.0  # A, C
    assert priorities(plan, SIGNATURES, LENGTHS, {"amount": 4}).priority["A"] == 1.0  # A alone


def test_a_branch_that_the_inputs_switch_off_costs_nothing() -> None:
    steps = (
        step("a")
        + step("b", "${inputs.amount} > 50", ["a"])
        + step("c", "${inputs.amount} > 50", ["b"])
        + step("d", None, ["a"])
    )
    plan = compile_plan(steps, "d")
    durations = {"a": 1.0, "b": 5.0, "c": 5.0, "d": 2.0}
    assert priorities(plan, SIGNATURES, durations, {"amount": 80}).priority["a"] == 11.0
    assert priorities(plan, SIGNATURES, durations, {"amount": 10}).priority["a"] == 3.0
    assert priorities(plan, SIGNATURES, durations).priority["a"] == 11.0  # unknown: it may run


def test_a_chain_is_not_counted_across_branches_that_exclude_each_other() -> None:
    """publish and the step behind it need decisions that cannot both be."""
    steps = (
        step("ap", action="approval.wait")
        + step("pub", "${ap.decision} == 'approved'", ["ap"])
        + step("back", "${ap.decision} == 'rejected'", ["pub"], "all_done")
    )
    plan = compile_plan(steps, "back")
    durations = {"ap": 1.0, "pub": 7.0, "back": 3.0}
    got = priorities(plan, SIGNATURES, durations)
    assert got.priority["pub"] == 7.0  # pub then back would need approved and rejected
    assert got.priority["ap"] == 8.0
    assert plain(plan, durations)["ap"] == 11.0


def test_a_step_that_never_runs_does_not_lengthen_the_chain_before_it() -> None:
    steps = step("a") + step("b", "${inputs.amount} > 100", ["a"], extra="")
    plan = compile_plan(steps, "a")
    got = priorities(plan, SIGNATURES, {"a": 1.0, "b": 50.0})
    assert got.priority["a"] == 1.0  # amount is at most 100


def test_a_guard_the_solver_cannot_read_counts_as_possible() -> None:
    steps = step("a") + step("b", "len('abc') > 2", ["a"])
    plan = compile_plan(steps, "b")
    assert priorities(plan, SIGNATURES, {"a": 1.0, "b": 4.0}).priority["a"] == 5.0


def test_a_chain_may_pass_over_a_skipped_step() -> None:
    steps = (
        step("a")
        + step("b", "${inputs.flag}", ["a"])
        + step("c", "not ${inputs.flag}", ["b"], "all_done")
    )
    plan = compile_plan(steps, "c")
    durations = {"a": 1.0, "b": 4.0, "c": 6.0}
    # b and c exclude each other, but a then c is a chain (b is skipped when c runs)
    assert priorities(plan, SIGNATURES, durations).priority["a"] == 7.0
    assert plain(plan, durations)["a"] == 11.0


def test_without_durations_every_step_takes_one() -> None:
    plan = compile_plan(step("a") + step("b", None, ["a"]) + step("c", None, ["b"]), "c")
    assert priorities(plan, SIGNATURES).priority == {"a": 3.0, "b": 2.0, "c": 1.0}


# -- the cap ----------------------------------------------------------------------------------
def fan(width: int) -> Plan:
    """A guarded step, then r (which runs whatever A did) with ``width`` guarded two-step tails."""
    steps = step("A", "${inputs.amount} <= 10") + step("r", None, ["A"], "all_done")
    for i in range(width):
        steps += step(f"m{i}", f"${{inputs.amount}} > {i * 5}", ["r"])
        steps += step(f"t{i}", f"${{inputs.amount}} > {i * 5 + 2}", [f"m{i}"])
    return compile_plan(steps, "t0")


def test_a_cut_is_reported_and_only_makes_a_priority_longer() -> None:
    plan = fan(8)
    durations = {s.id: 1.0 for s in plan.steps} | {"r": 100.0}
    durations |= {f"t{i}": 1.0 + i for i in range(8)}  # the longest tails need the largest amounts
    full = priorities(plan, SIGNATURES, durations, cap=10_000)
    capped = priorities(plan, SIGNATURES, durations, cap=2)
    assert not full.cut and capped.cut
    base = plain(plan, durations)
    for s in plan.steps:
        assert full.priority[s.id] <= capped.priority[s.id] <= base[s.id]
    # the two longest chains from r need amount > 32, which A (amount <= 10) rules out; the longest
    # one that was left out is kept under the condition of r alone, which A does not exclude
    assert full.priority["A"] < capped.priority["A"]


def test_the_cap_is_16_by_default_and_a_small_workflow_is_never_cut() -> None:
    assert CAP == 16
    assert not priorities(compile_plan(PROPOSAL, "C"), SIGNATURES).cut
