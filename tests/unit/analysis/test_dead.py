"""Tests of the dead-step check (US-23, C6): a guard that can never be true."""

from __future__ import annotations

from flowstat.analysis.guards import analyse
from flowstat.analysis.signatures import ActionSignature, Choices, Signatures
from flowstat.compiler.catalogue import Catalogue
from flowstat.compiler.compile import compile_workflow
from flowstat.compiler.plan import Plan
from flowstat.model.diagnostics import Diagnostic, Severity

SIGNATURES: Signatures = {
    "approval.wait": ActionSignature(
        {}, {"decision": Choices(("approved", "rejected"), "decisions")}
    ),
}
CATALOGUE = Catalogue({"regions": ({"code": "A", "name": "a"}, {"code": "B", "name": "b"})})
HEAD = (
    "workflow: demo\nversion: 1\ninputs:\n"
    "  amount: {type: int, min: 0, max: 100}\n"
    "  mode: {type: string, values: [fast, slow]}\n"
    "  ratio: {type: float, min: 0, max: 1}\n"
    "  free: {type: int}\n"
)
APPROVE = "  approve:\n    action: approval.wait\n    with: {role: chief}\n"


def step(name: str, when: str | None, needs: str = "approve") -> str:
    out = f"  {name}:\n    action: x.y\n"
    if needs:
        out += f'    needs: ["{needs}"]\n'
    if when is not None:
        out += f'    when: "{when}"\n'
    return out


def plan_of(steps: str) -> Plan:
    text = HEAD + "steps:\n" + APPROVE + steps + "goals: {any_of: [approve]}\n"
    result = compile_workflow(text, CATALOGUE)
    assert result.plan is not None, [d.message for d in result.diagnostics]
    return result.plan


def findings(steps: str) -> list[Diagnostic]:
    return analyse(plan_of(steps), SIGNATURES)


def dead(steps: str) -> list[str]:
    return [d.message.split("'")[1] for d in findings(steps) if d.code == "W-DEAD-001"]


def test_a_guard_beyond_the_declared_maximum_is_dead() -> None:
    assert dead(step("big", "${inputs.amount} > 100")) == ["big"]
    assert dead(step("edge", "${inputs.amount} >= 100")) == []
    assert dead(step("low", "${inputs.amount} < 0")) == ["low"]
    assert dead(step("zero", "${inputs.amount} <= 0")) == []


def test_a_value_that_is_not_allowed_makes_a_guard_dead() -> None:
    assert dead(step("a", "${inputs.mode} == 'medium'")) == ["a"]
    assert dead(step("b", "${inputs.mode} == 'fast'")) == []
    assert dead(step("c", "${inputs.mode} in ['medium', 'huge']")) == ["c"]
    assert dead(step("d", "${inputs.mode} in ['medium', 'slow']")) == []
    assert dead(step("e", "${inputs.mode} != 'fast' and ${inputs.mode} != 'slow'")) == ["e"]


def test_real_bounds() -> None:
    assert dead(step("a", "${inputs.ratio} > 1")) == ["a"]
    assert dead(step("b", "${inputs.ratio} >= 1")) == []
    assert dead(step("c", "${inputs.ratio} < 0")) == ["c"]
    assert dead(step("d", "${inputs.ratio} > 0.999")) == []


def test_a_guard_that_contradicts_itself_is_dead_without_any_constraint() -> None:
    assert dead(step("a", "${inputs.free} > 5 and ${inputs.free} < 3")) == ["a"]
    assert dead(step("b", "${inputs.free} > 5 and ${inputs.free} < 7")) == []


def test_a_comparison_of_different_types_is_never_true() -> None:
    assert dead(step("a", "${inputs.amount} == 'x'")) == ["a"]
    assert dead(step("b", "${inputs.amount} != 'x'")) == []


def test_an_opaque_part_does_not_hide_a_contradiction_nor_invent_one() -> None:
    assert dead(step("a", "len('a') > 0 and ${inputs.amount} > 100")) == ["a"]
    assert dead(step("b", "len('a') > 0 and ${inputs.amount} > 5")) == []
    assert dead(step("c", "len('a') > 0")) == []


def test_a_decision_that_the_approval_cannot_give() -> None:
    assert dead(step("a", "${approve.decision} == 'escalated'")) == ["a"]
    wider = APPROVE.replace("{role: chief}", "{role: chief, decisions: [approved, escalated]}")
    text = (
        HEAD
        + "steps:\n"
        + wider
        + step("a", "${approve.decision} == 'escalated'")
        + "goals: {any_of: [approve]}\n"
    )
    result = compile_workflow(text, CATALOGUE)
    assert result.plan is not None
    assert [d.code for d in analyse(result.plan, SIGNATURES)] == []


def test_the_message_names_the_step_and_the_guard_and_points_at_it() -> None:
    plan = plan_of(step("big", "${inputs.amount} > 100"))
    [d] = analyse(plan, SIGNATURES)
    assert d.code == "W-DEAD-001" and d.severity is Severity.WARNING
    assert "'big'" in d.message and "inputs.amount" in d.message
    when = plan.step("big").when
    assert when is not None and (d.line, d.column) == (when.line, when.column)


def test_a_step_without_a_guard_and_instances_of_a_foreach_step_are_not_checked() -> None:
    assert dead(step("plain", None)) == []
    steps = (
        "  load:\n    foreach: region in catalogue.regions\n    action: x.y\n"
        '    when: "${inputs.amount} > 100"\n'
    )
    assert dead(steps) == []


def test_a_dead_step_and_a_gap_are_both_reported() -> None:
    steps = step("small", "${inputs.amount} <= 50") + step("big", "${inputs.amount} > 100")
    got = {d.code for d in findings(steps)}
    assert got == {"W-DEAD-001", "W-BRANCH-002"}  # big never runs; 51 to 100 runs nothing


def test_findings_are_in_the_order_of_the_text() -> None:
    steps = step("a", "${inputs.amount} > 100") + step("b", "${inputs.mode} == 'x'")
    got = findings(steps)
    assert [(d.line, d.column) for d in got] == sorted((d.line, d.column) for d in got)
    assert dead(steps) == ["a", "b"]
