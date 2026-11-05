"""Acceptance tests of US-24: a generated minimal test set.

Given the example, when I request tests, the inputs produced execute and skip each guarded step at
least once where that is possible, are run with mocked actions, and any difference between the
predicted and the observed skips is reported.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flowstat.service.cli import main

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / "examples" / "quarterly_consolidation.yaml"
CATALOGUE = str(ROOT / "examples" / "catalogue.yaml")
NAME = "quarterly_consolidation"


def publish(tmp_path: Path, text: str | None = None) -> str:
    db = str(tmp_path / "flowstat.db")
    path = WORKFLOW
    if text is not None:
        path = tmp_path / "workflow.yaml"
        path.write_text(text, "utf8")
    assert main(["publish", "--db", db, "--catalogue", CATALOGUE, str(path)]) == 0
    return db


def test_the_example_gets_two_cases_and_no_difference(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = publish(tmp_path)
    capsys.readouterr()
    code = main(["tests", NAME, "--db", db, "--input", "quarter=2025Q1"])
    out = capsys.readouterr().out
    assert code == 0
    assert "2 cases" in out and "4 of 4" in out
    assert "approve.decision = 'approved'" in out and "approve.decision = 'rejected'" in out
    assert "publish runs" in out and "return_to_regions is skipped" in out
    assert "predicted and observed agree" in out


def test_a_step_that_can_never_run_is_said_to_be_impossible(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = (
        WORKFLOW.read_text("utf8")
        .replace("inputs:\n", "inputs:\n  amount: {type: int, min: 0, max: 100, default: 10}\n", 1)
        .replace(
            "goals:",
            "  audit:\n    action: email.send\n    needs: [approve]\n"
            '    when: "${inputs.amount} > 100"\n'
            "goals:",
            1,
        )
    )
    db = publish(tmp_path, text)
    capsys.readouterr()
    code = main(["tests", NAME, "--db", db, "--input", "quarter=2025Q1"])
    out = capsys.readouterr().out
    assert code == 0
    assert "audit runs" in out and "cannot happen" in out
    assert "no input" not in out  # it is proved impossible, not just not found


def test_an_input_that_no_guard_reads_comes_from_the_caller(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = publish(tmp_path)
    capsys.readouterr()
    assert main(["tests", NAME, "--db", db]) == 1  # quarter is required and nothing generates it
    out = capsys.readouterr().out
    assert "E-INPUT-001" in out


def test_an_unknown_workflow_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = publish(tmp_path)
    capsys.readouterr()
    assert main(["tests", "nothing", "--db", db]) == 2
    assert "nothing" in capsys.readouterr().err
