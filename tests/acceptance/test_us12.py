"""Acceptance test of US-12: a regional xlsx form is read, and results are written in steps."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from flowstat.actions.base import ActionContext
from flowstat.actions.registry import default_registry
from flowstat.engine.runner import Error, Ok, run_threaded
from flowstat.engine.scheduler import Scheduler, Start
from flowstat.model.states import RunOutcome, StepState
from tests.unit.actions.helpers import GOOD_ROWS, write_all_forms, write_form
from tests.unit.engine.helpers import example_plan


def context(tmp_path: Path, run_id: str = "run-1") -> ActionContext:
    return ActionContext(run_id, "", tmp_path / "data", str(tmp_path / "target.db"))


def test_us12_a_valid_form_gives_an_output_with_the_declared_schema(tmp_path: Path) -> None:
    write_form(tmp_path / "data" / "BIS" / "2025Q1.xlsx", GOOD_ROWS)
    run = default_registry().executor(context(tmp_path))

    outcome = run(Start("load[BIS]", "xlsx.read", {"path": "BIS/2025Q1.xlsx"}))

    assert isinstance(outcome, Ok)
    assert set(outcome.output) == {"rows", "count"}
    rows = outcome.output["rows"]
    assert isinstance(rows, list) and outcome.output["count"] == len(rows) == 3
    assert all(set(row) == {"code", "name", "value"} for row in rows)


def test_us12_a_missing_file_fails_the_step_with_the_path_in_the_message(tmp_path: Path) -> None:
    run = default_registry().executor(context(tmp_path))

    outcome = run(Start("load[BIS]", "xlsx.read", {"path": "BIS/2025Q1.xlsx"}))

    assert isinstance(outcome, Error)
    assert outcome.code == "E-ACT-002" and "BIS/2025Q1.xlsx" in outcome.message


def test_us12_the_example_workflow_runs_end_to_end_with_real_files(tmp_path: Path) -> None:
    write_all_forms(tmp_path / "data", "2025Q1", missing=["BIS"])
    scheduler = Scheduler(example_plan(), {"quarter": "2025Q1"}, 4)

    run_threaded(
        scheduler,
        default_registry().executor(context(tmp_path)),
        lambda: 0.0,
        lambda seconds: None,
        lambda command: {"decision": "approved"},
    )

    states = scheduler.states
    assert states["load[BIS]"] is StepState.FAILED
    assert "BIS/2025Q1.xlsx" in scheduler.errors["load[BIS]"][1]
    assert (states["consolidate"], states["publish"]) == (StepState.SUCCEEDED, StepState.SUCCEEDED)
    assert scheduler.outcome is RunOutcome.SUCCEEDED
    db = sqlite3.connect(tmp_path / "target.db")
    try:
        written = db.execute("SELECT COUNT(*) FROM published WHERE batch = 'run-1'").fetchone()
    finally:
        db.close()
    assert written == (3,)  # three indicators; `ensures` compared this with the aggregate
