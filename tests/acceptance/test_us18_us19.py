"""Acceptance tests of US-18 and US-19: the designer sees type errors before anything runs.

US-18: given `when: load.rows > 5` where rows is a list, an error says that a list cannot be
compared with a number, at that column, and no follow-up errors appear.
US-19: given consolidate takes `check[*].rows`, the parameter has the type list of rows; an action
that expects a single rows value gets an error that shows the mismatch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flowstat.actions.registry import default_registry
from flowstat.model.diagnostics import Severity
from flowstat.repo.db import open_database
from flowstat.service.cli import main
from flowstat.service.workflows import PublishError, WorkflowService

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = (ROOT / "examples" / "quarterly_consolidation.yaml").read_text(encoding="utf8")
CATALOGUE = str(ROOT / "examples" / "catalogue.yaml")
REGIONS = ["BIS", "OSC", "BAT", "JAL", "ISK", "NAR", "OSH", "TAL", "CHU"]
NAME = "quarterly_consolidation"

LIST_VS_NUMBER = EXAMPLE.replace(
    "  consolidate:\n",
    '  gate:\n    action: stats.aggregate\n    needs: ["check[*]"]\n'
    '    join: {at_least: 7}\n    when: "${check[*].rows} > 5"\n'
    '    with: {parts: "${check[*].rows}"}\n'
    "  consolidate:\n",
)
ONE_ROWS_VALUE = EXAMPLE.replace("action: stats.aggregate", "action: db.write").replace(
    "with: {parts:", "with: {rows:"
)


def service() -> WorkflowService:
    registry = default_registry()
    svc = WorkflowService(
        open_database(":memory:"), known_actions=registry.names(), signatures=registry.signatures()
    )
    svc.set_catalogue_list("regions", [{"code": c, "name": c.title()} for c in REGIONS])
    return svc


def test_a_list_compared_with_a_number_is_one_error_at_its_column() -> None:
    saved = service().save_draft(NAME, LIST_VS_NUMBER, "t1")
    assert [d.code for d in saved.diagnostics] == ["E-TYPE-001"]  # and no follow-up
    error = saved.diagnostics[0]
    assert error.severity is Severity.ERROR
    assert "a list cannot be compared with a number" in error.message.lower()
    line = LIST_VS_NUMBER.splitlines()[error.line - 1]
    assert line[error.column - 1 :].startswith("${check[*].rows} > 5")


def test_the_example_has_no_type_error_and_consolidate_takes_a_list_of_rows() -> None:
    saved = service().save_draft(NAME, EXAMPLE, "t1")
    assert saved.diagnostics == [] and saved.findings == []


def test_an_action_that_expects_a_single_rows_value_gets_an_error() -> None:
    saved = service().save_draft(NAME, ONE_ROWS_VALUE, "t1")
    errors = [d for d in saved.diagnostics if d.code == "E-TYPE-001"]
    assert len(errors) == 1
    assert "'rows' of db.write must be list<row>, not list<list<row>>" in errors[0].message
    line = ONE_ROWS_VALUE.splitlines()[errors[0].line - 1]
    assert line[errors[0].column - 1 :].startswith("${check[*].rows}")


def test_a_type_error_stops_a_publication() -> None:
    svc = service()
    saved = svc.save_draft(NAME, LIST_VS_NUMBER, "t1")
    assert saved.diagnostics
    with pytest.raises(PublishError) as raised:
        svc.publish(NAME, 1, "t2")
    assert [d.code for d in raised.value.diagnostics] == ["E-TYPE-001"]


def test_warnings_about_types_do_not_stop_a_publication() -> None:
    svc = service()
    text = EXAMPLE.replace(
        "with: {role: chief_statistician}", "with: {role: chief_statistician, x: 1}"
    )
    saved = svc.save_draft(NAME, text, "t1")
    assert saved.diagnostics == []
    assert [f.code for f in saved.findings] == ["W-TYPE-004"]
    svc.publish(NAME, 1, "t2")


def test_the_command_line_prints_the_error_and_does_not_publish(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = tmp_path / "bad.yaml"
    workflow.write_text(LIST_VS_NUMBER, "utf8")
    db = str(tmp_path / "flowstat.db")
    assert main(["publish", "--db", db, "--catalogue", CATALOGUE, str(workflow)]) == 1
    out = capsys.readouterr().out
    assert "error E-TYPE-001 line" in out and "cannot be compared" in out
    assert "published" not in out and "has errors" in out


def test_a_service_without_signatures_does_not_check_the_arguments() -> None:
    svc = WorkflowService(open_database(":memory:"))
    svc.set_catalogue_list("regions", [{"code": c, "name": c.title()} for c in REGIONS])
    saved = svc.save_draft(NAME, ONE_ROWS_VALUE, "t1")
    assert saved.diagnostics == []
