"""Acceptance tests of US-03, US-04 and US-05: the criteria of the stories, word for word."""

from __future__ import annotations

from flowstat.compiler.catalogue import Catalogue
from flowstat.compiler.compile import compile_workflow

CAT = Catalogue(
    {
        "regions": tuple(
            {"code": c} for c in ("BIS", "OSC", "BAT", "JAL", "ISK", "NAR", "OSH", "TAL", "CHU")
        )
    }
)
HEAD = "workflow: demo\nversion: 1\nsteps:\n"
GOALS = "goals: {any_of: [z]}\n"


def test_us03_missing_colon_points_to_its_line() -> None:
    lines = [
        "workflow: demo",
        "version: 1",
        "inputs:",
        "  quarter: {type: string}",
        "steps:",
        "  a:",
        "    action: x.y",
        "  b:",
        "    action: x.y",
        "  c:",
        "    action: x.y",
        "  d:",
        "    action: x.y",
        "    needs [a]",  # line 14: the colon is missing
        "goals: {any_of: [d]}",
    ]
    result = compile_workflow("\n".join(lines) + "\n", CAT)
    assert [d.code for d in result.diagnostics] == ["E-YAML-001"]
    assert result.diagnostics[0].line == 14 and result.diagnostics[0].column >= 1
    assert result.plan is None


def test_us03_duplicate_step_id_points_to_the_second() -> None:
    text = HEAD + "  z:\n    action: x.y\n  z:\n    action: x.y\n" + GOALS
    result = compile_workflow(text, CAT)
    assert [(d.code, d.line) for d in result.diagnostics] == [("E-YAML-002", 6)]


def test_us04_unknown_reference_points_to_it() -> None:
    text = HEAD + "  b:\n    action: x.y\n    needs: [c]\n" + "goals: {any_of: [b]}\n"
    result = compile_workflow(text, CAT)
    assert [(d.code, d.line, d.column) for d in result.diagnostics] == [("E-REF-001", 6, 13)]


def test_us04_cycle_is_one_error_with_the_path() -> None:
    text = (
        HEAD + "  a:\n    action: x.y\n    needs: [b]\n  b:\n    action: x.y\n    needs: [a]\n"
        "goals: {any_of: [a]}\n"
    )
    result = compile_workflow(text, CAT)
    assert [d.code for d in result.diagnostics] == ["E-GRAPH-001"]
    assert "a -> b -> a" in result.diagnostics[0].message


def test_us05_nine_regions_give_nine_instances() -> None:
    text = (
        HEAD + "  load:\n    foreach: region in catalogue.regions\n    action: x.y\n"
        '  z:\n    action: x.y\n    needs: ["load[*]"]\n' + GOALS
    )
    result = compile_workflow(text, CAT)
    assert result.plan is not None
    assert result.plan.instances_of("load") == tuple(
        f"load[{c}]" for c in ("BIS", "OSC", "BAT", "JAL", "ISK", "NAR", "OSH", "TAL", "CHU")
    )


def test_us05_template_error_is_reported_once_at_the_template_line() -> None:
    text = (
        HEAD + "  load:\n    foreach: region in catalogue.regions\n    action: x.y\n"
        '    with: {path: "${region.code"}\n'  # line 7: unterminated reference
        '  z:\n    action: x.y\n    needs: ["load[*]"]\n' + GOALS
    )
    result = compile_workflow(text, CAT)
    assert [(d.code, d.line) for d in result.diagnostics] == [("E-EXPR-002", 7)]
