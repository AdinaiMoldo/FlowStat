"""Acceptance tests of US-21 and US-22: the designer is warned about outputs that may not exist.

US-21: given c references approve, which has a when, a warning names the condition that can skip
approve; given the guard of c implies the guard of approve, there is no warning.
US-22: given any_of goals with guards that do not cover every case, a warning appears with a
witness value; given a reference that needs 8 elements of a group with at_least: 7, a warning
appears.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flowstat.actions.registry import default_registry
from flowstat.model.diagnostics import Severity
from flowstat.model.versions import VersionStatus
from flowstat.repo.db import open_database
from flowstat.service.cli import main
from flowstat.service.workflows import WorkflowService

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = (ROOT / "examples" / "quarterly_consolidation.yaml").read_text(encoding="utf8")
CATALOGUE = str(ROOT / "examples" / "catalogue.yaml")
REGIONS = ["BIS", "OSC", "BAT", "JAL", "ISK", "NAR", "OSH", "TAL", "CHU"]
NAME = "quarterly_consolidation"

PARTS = '    with: {parts: "${check[*].rows}"}\n'
EIGHTH = EXAMPLE.replace(PARTS, PARTS + '    ensures: "len(${check[*].rows}[7]) > 0"\n')
SEVENTH = EXAMPLE.replace(PARTS, PARTS + '    ensures: "len(${check[*].rows}[6]) > 0"\n')


def service() -> WorkflowService:
    registry = default_registry()
    svc = WorkflowService(
        open_database(":memory:"), known_actions=registry.names(), signatures=registry.signatures()
    )
    svc.set_catalogue_list("regions", [{"code": c, "name": c.title()} for c in REGIONS])
    return svc


def codes(text: str) -> list[str]:
    return [f.code for f in service().save_draft(NAME, text, "t1").findings]


def test_the_example_has_no_availability_finding() -> None:
    saved = service().save_draft(NAME, EXAMPLE, "t1")
    assert saved.diagnostics == [] and saved.findings == []


def test_a_reference_to_a_step_that_can_be_skipped_names_the_condition() -> None:
    text = EXAMPLE.replace(
        "    with: {role: chief_statistician}\n",
        "    with: {role: chief_statistician}\n    when: \"${inputs.quarter} != '2025Q1'\"\n",
    ).replace(
        "    needs: [approve]\n    when: \"${approve.decision} == 'rejected'\"",
        "    needs: [approve]\n    join: all_done\n    when: \"${approve.decision} == 'rejected'\"",
    )
    saved = service().save_draft(NAME, text, "t1")
    avail = [f for f in saved.findings if f.code == "W-AVAIL-001"]
    assert len(avail) == 1 and avail[0].severity is Severity.WARNING
    assert "approve" in avail[0].message and "2025Q1" in avail[0].message
    assert text.splitlines()[avail[0].line - 1].lstrip().startswith("when:")


def test_a_goal_that_may_be_skipped_comes_with_a_witness() -> None:
    narrower = EXAMPLE.replace(
        "${approve.decision} == 'approved'",
        "${approve.decision} == 'approved' and ${inputs.quarter} == '2025Q1'",
    )
    saved = service().save_draft(NAME, narrower, "t1")
    goal = [f for f in saved.findings if f.code == "W-GOAL-001"]
    assert len(goal) == 1 and "no goal is reached" in goal[0].message
    assert "inputs.quarter" in goal[0].message or "approve.decision" in goal[0].message


def test_eight_elements_of_a_group_when_seven_are_guaranteed() -> None:
    saved = service().save_draft(NAME, EIGHTH, "t1")
    found = [f for f in saved.findings if f.code == "W-AVAIL-002"]
    assert len(found) == 1
    assert "8" in found[0].message and "7" in found[0].message
    assert codes(SEVENTH) == []  # seven are guaranteed: the seventh element exists


def test_warnings_do_not_stop_a_publication() -> None:
    svc = service()
    svc.save_draft(NAME, EIGHTH, "t1")
    version = svc.publish(NAME, 1, "t2")
    assert version.status is VersionStatus.PUBLISHED
    assert [f.code for f in svc.findings(NAME, 1)] == ["W-AVAIL-002"]


def test_the_command_line_prints_the_warning_and_publishes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = tmp_path / "eighth.yaml"
    workflow.write_text(EIGHTH, "utf8")
    db = str(tmp_path / "flowstat.db")
    assert main(["publish", "--db", db, "--catalogue", CATALOGUE, str(workflow)]) == 0
    out = capsys.readouterr().out
    assert "warning W-AVAIL-002" in out
    assert out.index("warning") < out.index(f"published {NAME} v1")
