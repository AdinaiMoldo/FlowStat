"""Tests of the command line: publish a workflow, run it with real actions, read the report."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from flowstat.service.cli import main
from tests.unit.actions.helpers import write_all_forms
from tests.unit.engine.helpers import ROOT

WORKFLOW = str(ROOT / "examples" / "quarterly_consolidation.yaml")
CATALOGUE = str(ROOT / "examples" / "catalogue.yaml")
NAME = "quarterly_consolidation"


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.db = str(tmp_path / "flowstat.db")
        self.data = tmp_path / "data"
        self.target = str(tmp_path / "target.db")

    def publish(self) -> int:
        return main(["publish", "--db", self.db, "--catalogue", CATALOGUE, WORKFLOW])

    def run(self, *extra: str, run_id: str = "run-1", quarter: str = "2025Q1") -> int:
        where = ["--db", self.db, "--data", str(self.data), "--target", self.target]
        return main(
            ["run", *where, "--run-id", run_id, "--input", f"quarter={quarter}", *extra, NAME]
        )

    def rows(self, sql: str) -> list[tuple[object, ...]]:
        db = sqlite3.connect(self.target)
        try:
            return db.execute(sql).fetchall()
        except sqlite3.OperationalError:
            return []  # no action wrote to the database, so its tables do not exist
        finally:
            db.close()


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def test_publish_prints_the_version(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    assert env.publish() == 0
    assert f"published {NAME} v1" in capsys.readouterr().out


def test_publishing_a_broken_workflow_prints_diagnostics(
    env: Env, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text("workflow: demo\nversion: 1\nsteps:\n  a:\n    action: nope.do\n", "utf8")
    assert main(["publish", "--db", env.db, str(broken)]) == 1
    out = capsys.readouterr().out
    assert "E-" in out and "line" in out


def test_a_full_run_publishes_and_reports(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    write_all_forms(env.data, "2025Q1")
    env.publish()
    capsys.readouterr()
    assert env.run("--decision", "approved") == 0
    out = capsys.readouterr().out
    assert "SUCCEEDED" in out and "consolidate" in out and "publish" in out
    assert env.rows("SELECT batch, code, value FROM published ORDER BY code") == [
        ("run-1", "I01", 900.0),
        ("run-1", "I02", 364.5),
        ("run-1", "I03", 63.0),
    ]


def test_a_missing_form_fails_its_step_with_the_path_and_the_run_goes_on(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    write_all_forms(env.data, "2025Q1", missing=["BIS", "OSC"])
    env.publish()
    capsys.readouterr()
    assert env.run("--decision", "approved") == 0  # seven of nine is enough for the quorum
    out = capsys.readouterr().out
    assert "load[BIS]" in out and "BIS/2025Q1.xlsx" in out and "E-ACT-002" in out
    assert env.rows("SELECT code, value FROM published ORDER BY code")[0] == ("I01", 700.0)


def test_too_few_forms_is_not_reported_as_success(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    write_all_forms(env.data, "2025Q1", missing=["BIS", "OSC", "BAT"])
    env.publish()
    capsys.readouterr()
    assert env.run("--decision", "approved") == 1
    assert "GOAL_NOT_REACHED" in capsys.readouterr().out
    assert env.rows("SELECT * FROM published") == []


def test_a_rejection_returns_the_forms_to_the_regions(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    write_all_forms(env.data, "2025Q1")
    env.publish()
    capsys.readouterr()
    assert env.run("--decision", "rejected") == 0
    assert "SUCCEEDED" in capsys.readouterr().out
    assert env.rows("SELECT run_id, step FROM outbox") == [("run-1", "return_to_regions")]
    assert env.rows("SELECT COUNT(*) FROM published") == [(0,)]


def test_without_a_decision_the_run_waits_for_the_chief_statistician(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    write_all_forms(env.data, "2025Q1")
    env.publish()
    capsys.readouterr()
    assert env.run() == 3
    out = capsys.readouterr().out
    assert "WAITING" in out and "approve" in out


def test_the_report_of_a_stored_run_can_be_read_again(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    write_all_forms(env.data, "2025Q1")
    env.publish()
    capsys.readouterr()
    env.run("--decision", "approved")
    first = capsys.readouterr().out
    assert main(["report", "--db", env.db, "run-1"]) == 0
    again = capsys.readouterr().out
    assert first.strip() == again.strip()


def test_unknown_workflow_and_bad_inputs_are_usage_errors(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    assert env.run() == 2
    assert NAME in capsys.readouterr().err
    env.publish()
    capsys.readouterr()
    assert env.run(quarter="nope") == 2
    assert "E-INPUT" in capsys.readouterr().err
    assert main(["report", "--db", env.db, "nope"]) == 2


def test_a_draft_cannot_be_run(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    from flowstat.service.cli import open_services

    workflows, _ = open_services(env.db)
    workflows.save_draft(NAME, Path(WORKFLOW).read_text("utf8"), "2026-11-05T10:00:00Z")
    assert env.run() == 2
    assert "publish" in capsys.readouterr().err
