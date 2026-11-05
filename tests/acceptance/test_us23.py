"""Acceptance tests of US-23: the designer learns about steps that can never run.

Given a step whose guard contradicts the constraints on the inputs, when I validate, then the
step is named.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flowstat.actions.registry import default_registry
from flowstat.repo.db import open_database
from flowstat.service.cli import main
from flowstat.service.workflows import WorkflowService

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = (ROOT / "examples" / "quarterly_consolidation.yaml").read_text(encoding="utf8")
CATALOGUE = str(ROOT / "examples" / "catalogue.yaml")
REGIONS = ["BIS", "OSC", "BAT", "JAL", "ISK", "NAR", "OSH", "TAL", "CHU"]
NAME = "quarterly_consolidation"

WITH_AUDIT = EXAMPLE.replace(
    "inputs:\n", "inputs:\n  amount: {type: int, min: 0, max: 100, default: 10}\n", 1
).replace(
    "goals:",
    "  audit:\n    action: email.send\n    needs: [approve]\n"
    '    when: "${inputs.amount} > 100"\n'
    "goals:",
    1,
)
REACHABLE = WITH_AUDIT.replace("> 100", ">= 100")


def service() -> WorkflowService:
    registry = default_registry()
    svc = WorkflowService(
        open_database(":memory:"), known_actions=registry.names(), signatures=registry.signatures()
    )
    svc.set_catalogue_list("regions", [{"code": c, "name": c.title()} for c in REGIONS])
    return svc


def test_a_step_that_the_inputs_make_impossible_is_named() -> None:
    saved = service().save_draft(NAME, WITH_AUDIT, "t1")
    assert saved.diagnostics == []
    assert [f.code for f in saved.findings] == ["W-DEAD-001"]
    finding = saved.findings[0]
    assert "'audit'" in finding.message
    assert WITH_AUDIT.splitlines()[finding.line - 1].lstrip().startswith("when:")


def test_the_same_step_with_a_reachable_guard_is_not_reported() -> None:
    saved = service().save_draft(NAME, REACHABLE, "t1")
    assert saved.findings == []


def test_the_run_refuses_a_value_outside_the_declared_bounds() -> None:
    svc = service()
    svc.save_draft(NAME, WITH_AUDIT, "t1")
    svc.publish(NAME, 1, "t2")
    _, errors = svc.validate_inputs(NAME, 1, {"quarter": "2025Q1", "amount": 101})
    assert [e.code for e in errors] == ["E-INPUT-008"]
    resolved, errors = svc.validate_inputs(NAME, 1, {"quarter": "2025Q1"})
    assert errors == [] and resolved["amount"] == 10  # the default is inside the bounds


def test_the_command_line_names_the_dead_step(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = tmp_path / "audit.yaml"
    workflow.write_text(WITH_AUDIT, "utf8")
    db = str(tmp_path / "flowstat.db")
    assert main(["publish", "--db", db, "--catalogue", CATALOGUE, str(workflow)]) == 0
    out = capsys.readouterr().out
    assert "warning W-DEAD-001" in out and "'audit'" in out
