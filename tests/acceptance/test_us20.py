"""Acceptance tests of US-20: the designer is told when branches overlap or leave a case open.

Given publish guarded by decision approved and return_to_regions by rejected, there is no
warning. Given the decision can also be escalated, a warning names escalated as uncovered.
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

WIDER = EXAMPLE.replace(
    "with: {role: chief_statistician}",
    "with: {role: chief_statistician, decisions: [approved, rejected, escalated]}",
)
OVERLAPPING = EXAMPLE.replace(
    "when: \"${approve.decision} == 'rejected'\"", "when: \"${approve.decision} != 'rejected'\""
)


def service() -> WorkflowService:
    registry = default_registry()
    svc = WorkflowService(
        open_database(":memory:"), known_actions=registry.names(), signatures=registry.signatures()
    )
    svc.set_catalogue_list("regions", [{"code": c, "name": c.title()} for c in REGIONS])
    return svc


def test_the_registry_describes_the_outputs_the_analysis_may_read() -> None:
    signatures = default_registry().signatures()
    approval = signatures["approval.wait"]
    assert approval.choices["decision"].default == ("approved", "rejected")
    assert approval.choices["decision"].argument == "decisions"
    assert signatures["db.write"].fields == {"written": "int", "batch": "str"}
    # a list is declared (the mocked run needs it) but a guard cannot read it as a variable
    assert signatures["xlsx.read"].fields == {"rows": "list", "count": "int"}


def test_the_example_has_no_warning() -> None:
    saved = service().save_draft(NAME, EXAMPLE, "t1")
    assert saved.diagnostics == [] and saved.findings == []


def test_a_third_decision_is_reported_as_uncovered() -> None:
    saved = service().save_draft(NAME, WIDER, "t1")
    assert saved.diagnostics == []  # a draft with warnings is a good draft
    # the goals are open too (US-22), with the same value; here the branches are the subject
    assert [f.code for f in saved.findings] == ["W-BRANCH-002", "W-GOAL-001"]
    finding = saved.findings[0]
    assert finding.severity is Severity.WARNING
    assert "escalated" in finding.message
    assert WIDER.splitlines()[finding.line - 1].lstrip().startswith("when:")


def test_overlapping_guards_are_reported() -> None:
    saved = service().save_draft(NAME, OVERLAPPING, "t1")
    assert [f.code for f in saved.findings] == ["W-BRANCH-001", "W-BRANCH-002", "W-GOAL-001"]
    assert "approved" in saved.findings[0].message
    assert "rejected" in saved.findings[1].message


def test_warnings_do_not_stop_a_publication() -> None:
    svc = service()
    svc.save_draft(NAME, WIDER, "t1")
    version = svc.publish(NAME, 1, "t2")
    assert version.status is VersionStatus.PUBLISHED
    assert [f.code for f in svc.findings(NAME, 1)] == ["W-BRANCH-002", "W-GOAL-001"]


def test_the_command_line_prints_the_warning_and_publishes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = tmp_path / "wider.yaml"
    workflow.write_text(WIDER, "utf8")
    db = str(tmp_path / "flowstat.db")
    assert main(["publish", "--db", db, "--catalogue", CATALOGUE, str(workflow)]) == 0
    out = capsys.readouterr().out
    assert "warning W-BRANCH-002" in out and "escalated" in out
    assert f"published {NAME} v1" in out
    assert out.index("warning") < out.index("published")


def test_a_workflow_without_findings_prints_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = str(tmp_path / "flowstat.db")
    assert (
        main(
            [
                "publish",
                "--db",
                db,
                "--catalogue",
                CATALOGUE,
                str(ROOT / "examples" / "quarterly_consolidation.yaml"),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "warning" not in out and "info" not in out


def test_without_signatures_the_outputs_are_unknown_and_nothing_is_claimed() -> None:
    svc = WorkflowService(open_database(":memory:"))
    svc.set_catalogue_list("regions", [{"code": c, "name": c.title()} for c in REGIONS])
    saved = svc.save_draft(NAME, WIDER, "t1")
    assert [f.code for f in saved.findings] == ["I-BRANCH-003", "I-AVAIL-003"]
    assert all(f.severity is Severity.INFO for f in saved.findings)


def test_a_run_with_a_declared_third_decision_goes_through(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The extra argument reaches the action and does no harm; nothing runs for escalated."""
    from tests.unit.actions.helpers import write_all_forms

    workflow = tmp_path / "wider.yaml"
    workflow.write_text(WIDER, "utf8")
    db = str(tmp_path / "flowstat.db")
    data = tmp_path / "data"
    write_all_forms(data, "2025Q1")
    assert main(["publish", "--db", db, "--catalogue", CATALOGUE, str(workflow)]) == 0
    capsys.readouterr()
    code = main(
        ["run", "--db", db, "--data", str(data), "--target", str(tmp_path / "t.db"),
         "--run-id", "r1", "--input", "quarter=2025Q1", "--decision", "approved", NAME]
    )  # fmt: skip
    assert code == 0
    assert "SUCCEEDED" in capsys.readouterr().out
