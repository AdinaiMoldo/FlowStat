"""Shared helpers of the engine tests: compile a small workflow text into a plan."""

from __future__ import annotations

from pathlib import Path

from flowstat.compiler.catalogue import Catalogue, load_catalogue
from flowstat.compiler.compile import compile_workflow
from flowstat.compiler.plan import Plan
from flowstat.dsl.reader import read_yaml

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = (ROOT / "examples" / "quarterly_consolidation.yaml").read_text(encoding="utf8")
REGIONS = ["BIS", "OSC", "BAT", "JAL", "ISK", "NAR", "OSH", "TAL", "CHU"]


def catalogue() -> Catalogue:
    tree, _ = read_yaml((ROOT / "examples" / "catalogue.yaml").read_text(encoding="utf8"))
    assert tree is not None
    cat, diags = load_catalogue(tree)
    assert diags == []
    return cat


def plan_of(text: str) -> Plan:
    result = compile_workflow(text, catalogue())
    assert result.diagnostics == [], [(d.code, d.line, d.message) for d in result.diagnostics]
    assert result.plan is not None
    return result.plan


def doc(steps: str, goals: str = "goals: {any_of: [z]}\n", inputs: str = "") -> str:
    head = "workflow: demo\nversion: 1\n"
    if inputs:
        head += "inputs:\n" + inputs
    return head + "steps:\n" + steps + goals


def example_plan() -> Plan:
    return plan_of(EXAMPLE)
