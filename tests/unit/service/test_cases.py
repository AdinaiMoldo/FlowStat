"""Tests of the run of generated cases with mocked actions (US-24): predicted against observed."""

from __future__ import annotations

from flowstat.actions.registry import default_registry
from flowstat.analysis.generate import generate
from flowstat.service.cases import run_cases
from tests.unit.analysis.helpers import compile_plan, step
from tests.unit.engine.helpers import example_plan

SIGNATURES = default_registry().signatures()


def test_the_example_runs_as_predicted() -> None:
    plan = example_plan()
    cases = generate(plan, SIGNATURES)
    runs = run_cases(plan, cases, {"quarter": "2025Q1"}, SIGNATURES)
    assert len(runs) == 2
    assert all(r.differences == () for r in runs)
    by_decision = {r.case.outputs["approve"]["decision"]: r for r in runs}
    assert by_decision["approved"].observed["publish"] == "run"
    assert by_decision["approved"].observed["return_to_regions"] == "skip"
    assert by_decision["rejected"].observed["publish"] == "skip"
    assert by_decision["rejected"].observed["return_to_regions"] == "run"


def test_a_contract_that_fails_on_a_mocked_output_is_a_difference() -> None:
    steps = step(
        "a",
        action="stats.aggregate",
        extra='    with: {parts: []}\n    ensures: "${a.regions} > 0"\n',
    ) + step("b", "${inputs.flag}", ["a"])
    plan = compile_plan(steps, "b")
    runs = run_cases(plan, generate(plan, SIGNATURES), {}, SIGNATURES)
    assert runs, "a case is generated"
    differences = [d for r in runs for d in r.differences]
    assert differences, "the mocked output (regions: 0) breaks the contract of a"
    by_step = {d.step: d for d in differences}
    assert by_step["a"].predicted == "run" and by_step["a"].observed == "FAILED"
    assert set(by_step) <= {"a", "b"}
    assert all(d.observed in ("FAILED", "UPSTREAM_FAILED", "CANCELLED") for d in differences)


def test_mocked_outputs_follow_the_declared_types() -> None:
    steps = step("a", action="stats.aggregate", extra="    with: {parts: []}\n") + step(
        "b", "${a.regions} == 7", ["a"]
    )
    plan = compile_plan(steps, "b")
    cases = generate(plan, SIGNATURES)
    runs = run_cases(plan, cases, {}, SIGNATURES)
    assert [t.label for t in cases.targets] == ["b runs", "b is skipped"]
    assert any(r.case.outputs["a"]["regions"] == 7 for r in runs)  # the case sets the output
    assert all(r.differences == () for r in runs)
    ran = [r for r in runs if r.observed["b"] == "run"]
    assert ran, "the mocked action returned 7 and b ran"
