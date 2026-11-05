"""Acceptance test of US-14: a failed run is compensated in the right order."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from flowstat.engine.scheduler import Start
from flowstat.engine.simulation import Done, Fail, Result, simulate
from flowstat.model.states import RunOutcome, StepState
from flowstat.recovery.saga import compensation_blockers
from flowstat.service.cli import main
from tests.unit.actions.helpers import write_form
from tests.unit.engine.helpers import doc, plan_of

S = StepState


def test_us14_b_is_undone_before_a_and_the_run_is_compensated() -> None:
    # Given a, then b succeeded, both with compensate, and c failed under the compensate policy
    steps = (
        "  a:\n    action: x.y\n    compensate: x.undo_a\n"
        "  b:\n    action: x.y\n    needs: [a]\n    compensate: x.undo_b\n"
        "  c:\n    action: x.y\n    needs: [b]\n    on_failure: compensate\n"
    )

    def act(command: Start) -> Result:
        if command.compensation:
            return Done({"a": 4.0, "b": 1.0}[command.step], {})
        return Fail(1.0, "E-ACT-002", "c broke") if command.step == "c" else Done(1.0, {})

    plan = plan_of(doc(steps, "goals: {any_of: [c]}\n"))
    sim = simulate(plan, {}, 2, act, compensation_planner=compensation_blockers)

    # then compensation of b finishes before compensation of a starts and the run is COMPENSATED
    assert sim.undone["b"][1] <= sim.undone["a"][0]
    assert sim.scheduler.outcome is RunOutcome.COMPENSATED
    assert (sim.scheduler.states["a"], sim.scheduler.states["b"]) == (S.COMPENSATED, S.COMPENSATED)


WORKFLOW = """workflow: undo_demo
version: 1
steps:
  read:
    action: xlsx.read
    with: {path: "BIS/2025Q1.xlsx"}
  write:
    action: db.write
    needs: [read]
    with: {rows: "${read.rows}"}
    compensate: db.delete_batch
  check:
    action: statguard.validate
    needs: [write]
    with: {rows: "${read.rows}"}
    on_failure: compensate
goals: {any_of: [check]}
"""


def test_us14_with_real_actions_the_written_rows_are_removed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # a form with a negative value: db.write stores it, statguard.validate then fails
    write_form(
        tmp_path / "forms" / "BIS" / "2025Q1.xlsx", [("I01", "Population", 100), ("I02", "Bad", -5)]
    )
    workflow = tmp_path / "undo_demo.yaml"
    workflow.write_text(WORKFLOW, encoding="utf8")
    db, target = str(tmp_path / "flowstat.db"), str(tmp_path / "target.db")
    assert main(["publish", str(workflow), "--db", db]) == 0
    capsys.readouterr()

    where = ["--db", db, "--data", str(tmp_path / "forms"), "--target", target]
    code = main(["run", "undo_demo", *where, "--run-id", "r1"])

    out = capsys.readouterr().out
    assert code == 1 and "COMPENSATED" in out
    assert "check" in out and "E-ACT-005" in out  # the failure that started the undo
    assert "write" in out
    rows = sqlite3.connect(target).execute("SELECT COUNT(*) FROM published").fetchone()
    assert rows == (0,)  # written, then removed
