"""Tests of the compiler phases 0 to 3 (C1, level 2): expansion, symbols, references, cycles.

Structure tests run on the main example of the Project Proposal; error tests are golden documents
with markers ``⟦`` at the expected positions (line and column in the original file).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flowstat.compiler.catalogue import Catalogue, load_catalogue
from flowstat.compiler.compile import compile_workflow
from flowstat.compiler.plan import Plan
from flowstat.dsl.ast import Ref, dump
from flowstat.dsl.reader import read_yaml
from tests.unit.markers import MARK, split_markers

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = (ROOT / "examples" / "quarterly_consolidation.yaml").read_text(encoding="utf8")
REGIONS = ["BIS", "OSC", "BAT", "JAL", "ISK", "NAR", "OSH", "TAL", "CHU"]


def catalogue() -> Catalogue:
    tree, diags = read_yaml((ROOT / "examples" / "catalogue.yaml").read_text(encoding="utf8"))
    assert diags == [] and tree is not None
    cat, cat_diags = load_catalogue(tree)
    assert cat_diags == []
    return cat


def compiled() -> Plan:
    result = compile_workflow(EXAMPLE, catalogue())
    assert result.diagnostics == [], [d.message for d in result.diagnostics]
    assert result.plan is not None
    return result.plan


# -- the example of the Proposal -----------------------------------------------------------------
def test_catalogue_has_nine_regions() -> None:
    assert [r["code"] for r in catalogue().lists["regions"]] == REGIONS


def test_the_example_expands_to_22_steps() -> None:
    plan = compiled()
    assert len(plan.steps) == 22
    assert plan.name == "quarterly_consolidation" and plan.version == 4


def test_instances_follow_catalogue_order() -> None:
    plan = compiled()
    assert plan.instances_of("load") == tuple(f"load[{c}]" for c in REGIONS)
    assert plan.instances_of("publish") == ("publish",)


def test_dependencies_of_expanded_steps() -> None:
    plan = compiled()
    assert plan.step("check[OSC]").needs == ("load[OSC]",)
    assert plan.step("consolidate").needs == tuple(f"check[{c}]" for c in REGIONS)
    assert plan.step("consolidate").join.kind == "at_least"
    assert plan.step("consolidate").join.k == 7
    assert plan.step("load[BIS]").needs == ()


def test_steps_are_in_topological_order() -> None:
    plan = compiled()
    seen: set[str] = set()
    for step in plan.steps:
        assert set(step.needs) <= seen, step.id
        seen.add(step.id)


def test_source_map_points_back_to_the_original_step() -> None:
    plan = compiled()
    line = next(i for i, ln in enumerate(EXAMPLE.splitlines(), 1) if ln.startswith("  load:"))
    step = plan.step("load[OSC]")
    assert (step.base, step.line, step.column) == ("load", line, 3)
    assert step.loop == ("region", "OSC")
    assert plan.step("publish").loop is None


def test_loop_variable_is_replaced_inside_templates() -> None:
    parts = dict(compiled().step("load[BIS]").with_)["path"]
    assert parts[:2] == ("BIS", "/")
    assert isinstance(parts[2], Ref) and [p.value for p in parts[2].path] == ["inputs", "quarter"]
    assert parts[3] == ".xlsx"


def test_loop_variable_selects_one_instance() -> None:
    (ref,) = dict(compiled().step("check[BIS]").with_)["rows"]
    assert isinstance(ref, Ref)
    assert [(p.kind, p.value) for p in ref.path] == [("field", "load[BIS]"), ("field", "rows")]


def test_fan_in_reference_is_kept_for_the_engine() -> None:
    (ref,) = dict(compiled().step("consolidate").with_)["parts"]
    assert isinstance(ref, Ref)
    assert [(p.kind, p.value) for p in ref.path] == [
        ("field", "check"),
        ("star", "*"),
        ("field", "rows"),
    ]


def test_conditions_are_parsed_expressions() -> None:
    step = compiled().step("publish")
    assert step.when is not None and dump(step.when) == "(== ${approve.decision} 'approved')"
    assert step.ensures is not None
    assert step.retry is not None and (step.retry.max, step.retry.backoff) == (3, "exponential")
    assert step.compensate == "db.delete_batch"


def test_goals() -> None:
    plan = compiled()
    assert (plan.goal_kind, plan.goals) == ("any_of", ("publish", "return_to_regions"))


def test_ancestor_bitsets() -> None:
    plan = compiled()
    ancestors = plan.ancestors("publish")
    expected = {"approve", "consolidate", *plan.instances_of("check"), *plan.instances_of("load")}
    assert ancestors == expected
    assert plan.ancestors("load[BIS]") == set()
    assert "load[OSC]" not in plan.ancestors("check[BIS]")


def test_compilation_is_deterministic() -> None:
    assert compiled() == compiled()


def test_known_actions_are_accepted() -> None:
    known = frozenset({
        "xlsx.read", "statguard.validate", "stats.aggregate", "approval.wait",
        "db.write", "email.send", "db.delete_batch",
    })  # fmt: skip
    result = compile_workflow(EXAMPLE, catalogue(), known)
    assert result.diagnostics == []


# -- error documents ----------------------------------------------------------------------------
HEAD = "workflow: demo\nversion: 1\ninputs:\n  quarter: {type: string}\n"
A = "  a:\n    action: x.y\n"
GOALS = "goals: {any_of: [a]}\n"


def doc(steps: str, goals: str = GOALS) -> str:
    return HEAD + "steps:\n" + steps + goals


CASES: list[tuple[str, str, list[str]]] = [
    ("unknown step in needs", doc(A + "  b:\n    action: x.y\n    needs: [" + MARK + "zzz]\n"), ["E-REF-001"]),
    ("unknown input", doc("  a:\n    action: x.y\n    with: {p: \"" + MARK + "${inputs.nope}\"}\n"), ["E-REF-001"]),
    ("unknown step in a reference", doc("  a:\n    action: x.y\n    with: {p: \"" + MARK + "${nostep.x}\"}\n"), ["E-REF-001"]),
    ("unknown goal", doc(A, "goals: {any_of: [" + MARK + "nope]}\n"), ["E-REF-001"]),
    ("malformed needs item", doc(A + "  b:\n    action: x.y\n    needs: [\"" + MARK + "a b\"]\n"), ["E-REF-002"]),
    ("loop variable without foreach", doc(A + "  b:\n    action: x.y\n    needs: [\"" + MARK + "a[region]\"]\n"), ["E-REF-005"]),
    ("star on a plain step", doc(A + "  b:\n    action: x.y\n    needs: [\"" + MARK + "a[*]\"]\n"), ["E-REF-006"]),
    ("goal names a foreach step", doc("  a:\n    foreach: r in catalogue.regions\n    action: x.y\n", "goals: {any_of: [" + MARK + "a]}\n"), ["E-REF-009"]),
    ("quorum above dependencies", doc(A + "  b:\n    action: x.y\n    needs: [a]\n    join: {at_least: " + MARK + "3}\n"), ["E-REF-010"]),
    ("index on a step", doc("  a:\n    action: x.y\n    with: {p: \"" + MARK + "${a[0].x}\"}\n"), ["E-REF-011"]),
    ("unknown catalogue list", doc("  a:\n    foreach: " + MARK + "r in catalogue.nothing\n    action: x.y\n"), ["E-REF-012"]),
    ("unknown loop attribute", doc("  a:\n    foreach: r in catalogue.regions\n    action: x.y\n    with: {p: \"" + MARK + "${r.nme}\"}\n  b:\n    action: x.y\n", "goals: {any_of: [b]}\n"), ["E-REF-004"]),
    ("referenced step is not a dependency", doc(A + "  c:\n    action: x.y\n  b:\n    action: x.y\n    needs: [a]\n    when: \"" + MARK + "${c.x} == 1\"\n"), ["E-REF-007"]),
    ("cycle of two", doc("  " + MARK + "a:\n    action: x.y\n    needs: [b]\n  b:\n    action: x.y\n    needs: [a]\n"), ["E-GRAPH-001"]),
    ("self dependency", doc("  " + MARK + "a:\n    action: x.y\n    needs: [a]\n"), ["E-GRAPH-001"]),
    ("syntax error in a condition", doc("  a:\n    action: x.y\n    when: \"1 +" + MARK + "\"\n"), ["E-EXPR-001"]),
    ("unterminated reference in a value", doc("  a:\n    action: x.y\n    with: {p: \"x " + MARK + "${y\"}\n"), ["E-EXPR-002"]),
    ("shape error stops the compilation", "workflow: demo\nversion: " + MARK + "0\nsteps:\n" + A + GOALS, ["E-SHAPE-004"]),
    ("yaml error", "workflow: demo\nsteps: [" + MARK + "\n", ["E-YAML-001"]),
]  # fmt: skip


@pytest.mark.parametrize(("name", "text", "codes"), CASES, ids=[c[0] for c in CASES])
def test_error_document(name: str, text: str, codes: list[str]) -> None:
    clean, expected = split_markers(text)
    result = compile_workflow(clean, catalogue())
    got = [(d.code, d.line, d.column) for d in result.diagnostics]
    want = [(code, line, col) for code, (line, col) in zip(codes, expected, strict=True)]
    if name == "yaml error":  # the library reports the place where it noticed the problem
        assert [c for c, _, _ in got] == codes
    else:
        assert got == want, [d.message for d in result.diagnostics]
    assert result.plan is None


def test_empty_catalogue_list_is_an_error() -> None:
    steps = "  a:\n    foreach: r in catalogue.none\n    action: x.y\n  b:\n    action: x.y\n"
    result = compile_workflow(doc(steps, "goals: {any_of: [b]}\n"), Catalogue({"none": ()}))
    assert [d.code for d in result.diagnostics] == ["E-REF-003"]


def test_quorum_is_not_checked_against_a_list_that_failed() -> None:
    steps = (
        "  a:\n    foreach: r in catalogue.nothing\n    action: x.y\n"
        '  b:\n    action: x.y\n    needs: ["a[*]"]\n    join: {at_least: 2}\n'
    )
    result = compile_workflow(doc(steps, "goals: {any_of: [b]}\n"), catalogue())
    assert [d.code for d in result.diagnostics] == ["E-REF-012"]


def test_unknown_action_is_reported_with_a_registry() -> None:
    clean, expected = split_markers(doc("  a:\n    action: " + MARK + "x.y\n"))
    result = compile_workflow(clean, catalogue(), frozenset({"other.action"}))
    assert [(d.code, d.line, d.column) for d in result.diagnostics] == [("E-REF-008", *expected[0])]


def test_cycle_message_names_the_whole_cycle() -> None:
    text = doc(
        "  a:\n    action: x.y\n    needs: [c]\n  b:\n    action: x.y\n    needs: [a]\n  c:\n    action: x.y\n    needs: [b]\n"
    )
    result = compile_workflow(text, catalogue())
    assert [d.code for d in result.diagnostics] == ["E-GRAPH-001"]
    assert "a -> c -> b -> a" in result.diagnostics[0].message


def test_the_shortest_cycle_is_reported_not_the_first_found() -> None:
    steps = (
        "  a:\n    action: x.y\n    needs: [b, e]\n"
        "  b:\n    action: x.y\n    needs: [c]\n"
        "  c:\n    action: x.y\n    needs: [d]\n"
        "  d:\n    action: x.y\n    needs: [a]\n"
        "  e:\n    action: x.y\n    needs: [a]\n"
    )
    result = compile_workflow(doc(steps), catalogue())
    assert [d.code for d in result.diagnostics] == ["E-GRAPH-001"]
    message = result.diagnostics[0].message
    assert message.count("->") == 2 and ("e" in message.split(":")[1])


def test_two_independent_cycles_are_both_reported() -> None:
    steps = (
        "  a:\n    action: x.y\n    needs: [b]\n  b:\n    action: x.y\n    needs: [a]\n"
        "  c:\n    action: x.y\n    needs: [d]\n  d:\n    action: x.y\n    needs: [c]\n"
    )
    result = compile_workflow(doc(steps), catalogue())
    assert [d.code for d in result.diagnostics] == ["E-GRAPH-001", "E-GRAPH-001"]


def test_errors_of_several_phases_are_reported_in_one_run() -> None:
    steps = (
        "  a:\n    action: x.y\n    needs: [b]\n  b:\n    action: x.y\n    needs: [a]\n"
        "  c:\n    action: x.y\n    needs: [missing]\n"
    )
    result = compile_workflow(doc(steps), catalogue())
    assert sorted(d.code for d in result.diagnostics) == ["E-GRAPH-001", "E-REF-001"]


def test_property_mutated_documents_never_crash_the_compiler() -> None:
    """Delete, duplicate and corrupt lines of the example: the result is a plan or diagnostics."""
    import random

    seed = 20261103
    rng = random.Random(seed)
    cat = catalogue()
    lines = EXAMPLE.splitlines()
    plans = errors = 0
    for _ in range(400):
        mutated = list(lines)
        for _ in range(rng.randint(1, 3)):
            i = rng.randrange(len(mutated))
            kind = rng.choice(["delete", "duplicate", "corrupt", "swap"])
            if kind == "delete":
                del mutated[i]
            elif kind == "duplicate":
                mutated.insert(i, mutated[i])
            elif kind == "swap" and len(mutated) > 1:
                j = rng.randrange(len(mutated))
                mutated[i], mutated[j] = mutated[j], mutated[i]
            else:
                col = rng.randrange(len(mutated[i]) + 1)
                mutated[i] = mutated[i][:col] + rng.choice("[]{}:$\"' -*,#&") + mutated[i][col:]
            if not mutated:
                mutated = ["x"]
        result = compile_workflow("\n".join(mutated) + "\n", cat)
        assert (result.plan is None) == (result.diagnostics != []), f"seed={seed}"
        for d in result.diagnostics:
            assert d.line >= 1 and d.column >= 1, f"seed={seed} {d}"
        plans += result.plan is not None
        errors += result.plan is None
    assert plans > 0 and errors > 0


# -- catalogue ---------------------------------------------------------------------------------
def _catalogue_errors(text: str) -> list[str]:
    tree, read_diags = read_yaml(text)
    assert read_diags == [] and tree is not None
    return [d.code for d in load_catalogue(tree)[1]]


def test_catalogue_errors() -> None:
    assert _catalogue_errors("regions: 5\n") == ["E-CAT-001"]
    assert _catalogue_errors("- a\n") == ["E-CAT-001"]
    assert _catalogue_errors("regions: [5]\n") == ["E-CAT-002"]
    assert _catalogue_errors("regions: [{name: x}]\n") == ["E-CAT-003"]
    assert _catalogue_errors("regions: [{code: a}, {code: a}]\n") == ["E-CAT-004"]
    assert _catalogue_errors("regions: [{code: a}, {code: b}]\n") == []
