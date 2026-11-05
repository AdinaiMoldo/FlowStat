"""Tests of the analysis of branches (US-20, C6): overlap, gaps, and what is left unsaid."""

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
    "stats.aggregate": ActionSignature({"regions": "int", "ratio": "real", "ok": "bool"}),
    "xlsx.read": ActionSignature({"count": "int"}),
}
CATALOGUE = Catalogue({"regions": ({"code": "A", "name": "a"}, {"code": "B", "name": "b"})})

HEAD = (
    "workflow: demo\nversion: 1\ninputs:\n"
    "  amount: {type: int}\n  ratio: {type: float}\n  flag: {type: bool}\n  note: {type: string}\n"
)
APPROVE = "  approve:\n    action: approval.wait\n    with: {role: chief}\n"
APPROVE3 = (
    "  approve:\n    action: approval.wait\n"
    "    with: {role: chief, decisions: [approved, rejected, escalated]}\n"
)


def step(name: str, when: str | None, needs: str = "approve", action: str = "x.y") -> str:
    out = f"  {name}:\n    action: {action}\n"
    if needs:
        quoted = ", ".join(f'"{n.strip()}"' for n in needs.split(","))
        out += f"    needs: [{quoted}]\n"
    if when is not None:
        out += f'    when: "{when}"\n'
    return out


def plan_of(steps: str, goal: str = "approve") -> Plan:
    text = HEAD + "steps:\n" + steps + f"goals: {{any_of: [{goal}]}}\n"
    result = compile_workflow(text, CATALOGUE)
    assert result.plan is not None, [d.message for d in result.diagnostics]
    return result.plan


def run(steps: str) -> list[Diagnostic]:
    return analyse(plan_of(steps), SIGNATURES)


def codes(diags: list[Diagnostic]) -> list[str]:
    return [d.code for d in diags]


DECISION = "${approve.decision}"


# -- US-20: the example ------------------------------------------------------------------------
def test_publish_and_return_to_regions_give_no_warning() -> None:
    diags = run(
        APPROVE
        + step("publish", f"{DECISION} == 'approved'")
        + step("return_to_regions", f"{DECISION} == 'rejected'")
    )
    assert diags == []


def test_an_extra_decision_is_named_as_uncovered() -> None:
    steps = (
        APPROVE3
        + step("publish", f"{DECISION} == 'approved'")
        + step("return_to_regions", f"{DECISION} == 'rejected'")
    )
    diags = run(steps)
    assert codes(diags) == ["W-BRANCH-002"]
    d = diags[0]
    assert d.severity is Severity.WARNING
    assert "escalated" in d.message and "approve.decision" in d.message
    assert "'publish'" in d.message and "'return_to_regions'" in d.message
    assert (d.line, d.column) == _guard_position(plan_of(steps), "publish")


def _guard_position(plan: Plan, step_id: str) -> tuple[int, int]:
    when = plan.step(step_id).when
    assert when is not None
    return when.line, when.column


def test_guards_that_can_both_hold_are_reported_with_a_case() -> None:
    steps = (
        APPROVE
        + step("publish", f"{DECISION} == 'approved'")
        + step("archive", f"{DECISION} != 'rejected'")
    )
    diags = run(steps)
    # with two decisions the guards are the same: both run on approved, nothing runs on rejected
    assert codes(diags) == ["W-BRANCH-001", "W-BRANCH-002"]
    assert "'publish'" in diags[0].message and "'archive'" in diags[0].message
    assert "approve.decision = 'approved'" in diags[0].message
    assert "approve.decision = 'rejected'" in diags[1].message


def test_a_guard_that_is_a_list_membership() -> None:
    steps = (
        APPROVE3
        + step("go", f"{DECISION} in ['approved', 'escalated']")
        + step("back", f"{DECISION} == 'rejected'")
    )
    assert run(steps) == []


def test_all_three_decisions_overlapping_and_a_gap_are_both_reported() -> None:
    steps = (
        APPROVE3
        + step("a", f"{DECISION} in ['approved', 'rejected']")
        + step("b", f"{DECISION} == 'rejected'")
    )
    assert codes(run(steps)) == ["W-BRANCH-001", "W-BRANCH-002"]


# -- numbers -----------------------------------------------------------------------------------
def test_a_threshold_that_leaves_the_boundary_uncovered() -> None:
    steps = step("small", "${inputs.amount} < 100", needs="") + step(
        "big", "${inputs.amount} > 100", needs=""
    )
    diags = analyse(plan_of(steps, goal="small"), SIGNATURES)
    assert codes(diags) == ["W-BRANCH-002"]
    assert "inputs.amount = 100" in diags[0].message


def test_a_threshold_that_covers_everything() -> None:
    steps = step("small", "${inputs.amount} < 100", needs="") + step(
        "big", "${inputs.amount} >= 100", needs=""
    )
    assert analyse(plan_of(steps, goal="small"), SIGNATURES) == []


def test_closed_ends_on_both_sides_overlap_at_the_boundary() -> None:
    steps = step("small", "${inputs.amount} <= 100", needs="") + step(
        "big", "${inputs.amount} >= 100", needs=""
    )
    diags = analyse(plan_of(steps, goal="small"), SIGNATURES)
    assert codes(diags) == ["W-BRANCH-001"]
    assert "inputs.amount = 100" in diags[0].message


def test_a_gap_between_reals_is_a_decimal() -> None:
    steps = step("low", "${inputs.ratio} < 0.5", needs="") + step(
        "high", "${inputs.ratio} > 0.5", needs=""
    )
    diags = analyse(plan_of(steps, goal="low"), SIGNATURES)
    assert codes(diags) == ["W-BRANCH-002"]
    assert "inputs.ratio = 0.5" in diags[0].message


def test_integers_have_no_gap_between_neighbours() -> None:
    steps = step("low", "${inputs.amount} < 5", needs="") + step(
        "high", "${inputs.amount} > 4", needs=""
    )
    assert analyse(plan_of(steps, goal="low"), SIGNATURES) == []


def test_a_difference_of_two_variables() -> None:
    steps = step("a", "${inputs.amount} - ${approve.n} > 0", needs="approve") + step(
        "b", "${inputs.amount} - ${approve.n} < 0", needs="approve"
    )
    plan = plan_of(APPROVE.replace("approval.wait", "stats.aggregate") + steps)
    sigs: Signatures = {"stats.aggregate": ActionSignature({"n": "int"})}
    diags = analyse(plan, sigs)
    assert codes(diags) == ["W-BRANCH-002"]
    assert "inputs.amount" in diags[0].message and "approve.n" in diags[0].message


def test_booleans() -> None:
    steps = step("on", "${inputs.flag}", needs="") + step("off", "not ${inputs.flag}", needs="")
    assert analyse(plan_of(steps, goal="on"), SIGNATURES) == []
    steps = step("on", "${inputs.flag}", needs="") + step(
        "also", "${inputs.flag} == true", needs=""
    )
    got = analyse(plan_of(steps, goal="on"), SIGNATURES)
    assert codes(got) == ["W-BRANCH-001", "W-BRANCH-002"]  # both true together; neither when false
    assert "inputs.flag = false" in got[1].message


# -- what is not a branch ----------------------------------------------------------------------
def test_one_guarded_step_is_an_optional_step() -> None:
    assert run(APPROVE + step("publish", f"{DECISION} == 'approved'")) == []


def test_guards_over_different_variables_are_independent_choices() -> None:
    steps = APPROVE + step("a", f"{DECISION} == 'approved'") + step("b", "${inputs.amount} > 5")
    assert run(steps) == []


def test_steps_with_different_needs_are_not_alternatives() -> None:
    steps = (
        APPROVE
        + "  other:\n    action: x.y\n"
        + step("a", f"{DECISION} == 'approved'")
        + step("b", f"{DECISION} == 'approved'", needs="other, approve")
    )
    assert run(steps) == []


def test_instances_of_one_foreach_step_are_not_compared() -> None:
    steps = (
        "  load:\n    foreach: region in catalogue.regions\n    action: xlsx.read\n"
        '    with: {path: "${region.code}"}\n    when: "${inputs.amount} > 5"\n'
        + step("after", None, needs="load[*]")
    )
    assert analyse(plan_of(steps, goal="after"), SIGNATURES) == []


# -- what the analysis does not claim ----------------------------------------------------------
def test_a_guard_outside_the_fragment_gives_a_note_and_no_warning() -> None:
    steps = (
        APPROVE
        + step("a", f"{DECISION} == 'approved'")
        + step("b", f"len('x') > 0 and {DECISION} == 'rejected'")
    )
    diags = run(steps)
    assert codes(diags) == ["I-BRANCH-003"]
    assert diags[0].severity is Severity.INFO
    assert "'b'" in diags[0].message


def test_a_clean_pair_is_still_reported_when_a_third_guard_is_opaque() -> None:
    steps = (
        APPROVE
        + step("a", f"{DECISION} == 'approved'")
        + step("b", f"{DECISION} != 'rejected'")
        + step("c", f"len('x') > 0 and {DECISION} == 'rejected'")
    )
    assert codes(run(steps)) == ["W-BRANCH-001", "I-BRANCH-003"]


def test_an_output_of_an_unknown_action_is_not_a_variable() -> None:
    steps = (
        "  src:\n    action: unknown.thing\n"
        + step("a", "${src.value} == 'x'", needs="src")
        + step("b", "${src.value} != 'x'", needs="src")
    )
    diags = analyse(plan_of(steps, goal="src"), SIGNATURES)
    assert codes(diags) == ["I-BRANCH-003"]


def test_a_variable_with_no_declared_choices_is_a_free_string() -> None:
    """``note`` is a string input: any text, so two equalities do not cover it."""
    steps = step("a", "${inputs.note} == 'x'", needs="") + step(
        "b", "${inputs.note} == 'y'", needs=""
    )
    diags = analyse(plan_of(steps, goal="a"), SIGNATURES)
    assert codes(diags) == ["W-BRANCH-002"]


def test_the_analysis_is_deterministic() -> None:
    steps = (
        APPROVE3 + step("a", f"{DECISION} == 'approved'") + step("b", f"{DECISION} != 'rejected'")
    )
    assert run(steps) == run(steps)


def test_the_choices_of_an_action_are_its_default_without_the_argument() -> None:
    sigs: Signatures = {
        "approval.wait": ActionSignature({}, {"decision": Choices(("yes", "no"), "options")})
    }
    steps = APPROVE + step("a", f"{DECISION} == 'yes'") + step("b", f"{DECISION} == 'no'")
    assert analyse(plan_of(steps), sigs) == []
