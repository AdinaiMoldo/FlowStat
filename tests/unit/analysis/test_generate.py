"""Tests of the generator of test inputs (US-24, C6)."""

from __future__ import annotations

import itertools
import random

from flowstat.analysis.generate import Target, generate
from flowstat.analysis.predict import RUN, SKIP, predict
from flowstat.analysis.variables import lower_guards
from tests.unit.analysis.helpers import SIGNATURES, compile_plan, random_workflow, step
from tests.unit.engine.helpers import example_plan

DECISION = "${approve.decision}"
APPROVE = step("approve", action="approval.wait", extra="    with: {role: chief}\n")


def labels(targets: tuple[Target, ...]) -> list[str]:
    return [t.label for t in targets]


# -- the example ------------------------------------------------------------------------------
def test_the_example_needs_two_cases_that_run_and_skip_each_branch() -> None:
    result = generate(example_plan(), SIGNATURES)
    assert labels(result.targets) == [
        "publish runs",
        "publish is skipped",
        "return_to_regions runs",
        "return_to_regions is skipped",
    ]
    assert len(result.cases) == 2
    assert result.infeasible == () and result.missed == ()
    decisions = {c.outputs["approve"]["decision"] for c in result.cases}
    assert decisions == {"approved", "rejected"}
    assert all(c.inputs == {} for c in result.cases)  # no guard reads an input
    covered = {t for c in result.cases for t in c.covers}
    assert covered == set(result.targets)


def test_a_third_decision_adds_no_case() -> None:
    wider = APPROVE.replace(
        "{role: chief}", "{role: chief, decisions: [approved, rejected, escalated]}"
    )
    steps = (
        wider
        + step("publish", f"{DECISION} == 'approved'", ["approve"])
        + step("back", f"{DECISION} == 'rejected'", ["approve"])
    )
    result = generate(compile_plan(steps, "publish"), SIGNATURES)
    assert len(result.cases) == 2 and result.missed == ()


def test_a_decision_that_is_the_only_way_to_reach_a_step() -> None:
    wider = APPROVE.replace("{role: chief}", "{role: chief, decisions: [approved, escalated]}")
    steps = wider + step("esc", f"{DECISION} == 'escalated'", ["approve"])
    result = generate(compile_plan(steps, "esc"), SIGNATURES)
    decisions = sorted(str(c.outputs["approve"]["decision"]) for c in result.cases)
    assert decisions == ["approved", "escalated"]


# -- numbers and the domains of the inputs ----------------------------------------------------
def test_a_threshold_is_run_and_skipped_inside_the_declared_bounds() -> None:
    result = generate(compile_plan(step("big", "${inputs.amount} > 60"), "big"), SIGNATURES)
    assert len(result.cases) == 2 and result.missed == ()
    amounts = sorted(int(str(c.inputs["amount"])) for c in result.cases)
    assert 0 <= amounts[0] <= 60 < amounts[1] <= 100


def test_a_target_that_the_declared_bounds_forbid_is_proved_infeasible() -> None:
    result = generate(compile_plan(step("big", "${inputs.amount} > 100"), "big"), SIGNATURES)
    assert labels(result.infeasible) == ["big runs"]
    assert result.missed == ()
    assert len(result.cases) == 1 and result.cases[0].covers == (Target("big", False),)


def test_a_guard_that_always_holds_cannot_be_skipped() -> None:
    result = generate(compile_plan(step("a", "${inputs.amount} >= 0"), "a"), SIGNATURES)
    assert labels(result.infeasible) == ["a is skipped"]


def test_reals_and_enumerations_of_the_inputs() -> None:
    steps = (
        step("low", "${inputs.ratio} < 0.5")
        + step("slow", "${inputs.mode} == 'slow'")
        + step("both", "${inputs.ratio} < 0.5 and ${inputs.mode} == 'slow'")
    )
    result = generate(compile_plan(steps, "low"), SIGNATURES)
    assert result.missed == () and result.infeasible == ()
    assert len(result.cases) == 2  # one with all three running, one with none
    for case in result.cases:
        assert 0 <= float(case.inputs["ratio"]) <= 1  # type: ignore[arg-type]
        assert case.inputs["mode"] in ("fast", "slow", "off")


# -- chains and joins -------------------------------------------------------------------------
def test_a_step_behind_a_guarded_step_needs_its_ancestors_to_run() -> None:
    steps = step("a", "${inputs.flag}") + step("b", "${inputs.amount} > 5", ["a"])
    result = generate(compile_plan(steps, "b"), SIGNATURES)
    assert result.missed == () and result.infeasible == ()
    ran = [c for c in result.cases if Target("b", True) in c.covers]
    assert ran and ran[0].inputs["flag"] is True and ran[0].inputs["amount"] > 5  # type: ignore[operator]


def test_a_quorum_of_guarded_steps() -> None:
    steps = (
        step("a", "${inputs.flag}")
        + step("b", "${inputs.amount} > 50")
        + step("c", "${inputs.mode} == 'fast'")
        + step("d", "${inputs.ratio} > 0.5", ["a", "b", "c"], "{at_least: 2}")
    )
    result = generate(compile_plan(steps, "d"), SIGNATURES)
    assert result.missed == () and result.infeasible == ()
    assert any(Target("d", True) in c.covers for c in result.cases)


def test_a_step_that_can_never_run_because_an_ancestor_is_dead() -> None:
    steps = step("a", "${inputs.amount} > 100") + step("b", "${inputs.flag}", ["a"])
    result = generate(compile_plan(steps, "b"), SIGNATURES)
    assert set(labels(result.infeasible)) == {"a runs", "b runs"}  # the exact condition of C2


def test_independent_guards_share_cases() -> None:
    steps = (
        step("a", "${inputs.flag}")
        + step("b", "${inputs.amount} > 5")
        + step("c", "${inputs.mode} == 'fast'")
    )
    result = generate(compile_plan(steps, "a"), SIGNATURES)
    assert len(result.cases) == 2 and result.missed == ()


# -- what is not claimed ----------------------------------------------------------------------
def test_a_guard_outside_the_fragment_is_missed_and_nothing_is_proved() -> None:
    result = generate(compile_plan(step("a", "len('x') > 0"), "a"), SIGNATURES)
    assert result.cases == () and result.infeasible == ()
    assert labels(result.missed) == ["a runs", "a is skipped"]


def test_steps_of_a_foreach_and_steps_without_guards_are_not_targets() -> None:
    steps = (
        "  load:\n    foreach: region in catalogue.regions\n    action: x.y\n"
        '    when: "${inputs.flag}"\n' + step("plain")
    )
    result = generate(compile_plan(steps, "plain"), SIGNATURES)
    assert result.targets == () and result.cases == ()


def test_the_generator_is_deterministic() -> None:
    steps = step("a", "${inputs.flag}") + step("b", "${inputs.amount} > 5", ["a"])
    plan = compile_plan(steps, "b")
    assert generate(plan, SIGNATURES) == generate(plan, SIGNATURES)


# -- checked by brute force -------------------------------------------------------------------
AMOUNTS = [0, 1, 24, 25, 26, 49, 50, 51, 74, 75, 76, 99, 100]
GRID = [
    {"inputs.amount": a, "inputs.mode": m, "inputs.ratio": r, "inputs.flag": f}
    for a, m, r, f in itertools.product(
        AMOUNTS, ["fast", "slow", "off"], [0.0, 0.25, 0.5, 0.75, 1.0], [False, True]
    )
]


def test_random_workflows_every_target_is_covered_or_proved_impossible() -> None:
    rng = random.Random(20261105)
    covered = infeasible = 0
    for _ in range(60):
        plan = random_workflow(rng)
        preds = lower_guards(plan, SIGNATURES).preds
        result = generate(plan, SIGNATURES)
        assert result.missed == ()
        assert set(result.targets) == (
            {t for c in result.cases for t in c.covers} | set(result.infeasible)
        )
        for case in result.cases:  # what a case claims is what the prediction says
            values = {f"inputs.{k}": v for k, v in case.inputs.items()}
            full = {**GRID[0], **values}
            got = predict(plan, preds, full)
            for target in case.covers:
                assert got[target.step] == (RUN if target.runs else SKIP), (plan, target, case)
        for target in result.infeasible:  # the proof: no assignment reaches it
            wanted = RUN if target.runs else SKIP
            assert not any(predict(plan, preds, a)[target.step] == wanted for a in GRID), target
        covered += len(result.targets) - len(result.infeasible)
        infeasible += len(result.infeasible)
    assert covered > 100 and infeasible > 5  # the sample has both kinds


def test_random_workflows_the_set_is_small() -> None:
    """A case per target would be the naive answer; the greedy cover is far smaller."""
    rng = random.Random(5)
    naive = chosen = 0
    for _ in range(60):
        result = generate(random_workflow(rng, guard_chance=0.9), SIGNATURES)
        naive += len(result.targets) - len(result.infeasible)
        chosen += len(result.cases)
    assert chosen < 0.7 * naive
