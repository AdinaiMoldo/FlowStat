"""Acceptance test of US-11: a finished run shows status, start, end and error of each step."""

from __future__ import annotations

from pathlib import Path

from flowstat.compiler.catalogue import load_catalogue
from flowstat.dsl.reader import read_yaml
from flowstat.engine.scheduler import Start
from flowstat.engine.simulation import Done, Fail, Result, simulate
from flowstat.model.states import RunOutcome, StepState
from flowstat.repo.db import open_database
from flowstat.service.runs import RunService
from flowstat.service.workflows import WorkflowService
from tests.unit.engine.helpers import ROOT

T = "2026-11-05T10:00:00Z"
S = StepState


def prepare(path: str) -> RunService:
    db = open_database(path)
    workflows = WorkflowService(db)
    tree, _ = read_yaml((ROOT / "examples" / "catalogue.yaml").read_text(encoding="utf8"))
    assert tree is not None
    catalogue, _ = load_catalogue(tree)
    for name, items in catalogue.lists.items():
        workflows.set_catalogue_list(name, [dict(i) for i in items])
    text = (ROOT / "examples" / "quarterly_consolidation.yaml").read_text(encoding="utf8")
    saved = workflows.save_draft("quarterly_consolidation", text, T)
    workflows.publish("quarterly_consolidation", saved.version.number, T)
    return RunService(db, workflows)


def test_us11_a_finished_run_shows_status_start_end_and_error_of_every_step(tmp_path: Path) -> None:
    path = str(tmp_path / "flowstat.db")
    runs = prepare(path)

    def act(command: Start) -> Result:
        if command.step in {"load[BIS]", "load[OSC]"}:
            return Fail(2.0, "E-ACT-002", f"cannot read {command.args['path']}")
        return Done(1.0, {"rows": [1, 2], "written": 2, "decision": "approved"})

    runs.execute(
        "run-001",
        "quarterly_consolidation",
        1,
        {"quarter": "2025Q1"},
        T,
        lambda plan, inputs: simulate(plan, inputs, 9, act).scheduler.events,
    )
    runs.workflows.versions_store.db.close()

    # when I open the run (a new process, the same database file)
    report = RunService(open_database(path), WorkflowService(open_database(path))).report("run-001")

    assert report.status == "FINISHED" and report.outcome is RunOutcome.SUCCEEDED
    by_step = {s.step: s for s in report.steps}
    bis = by_step["load[BIS]"]
    assert bis.state is S.FAILED
    assert (bis.started_at, bis.finished_at) == (0.0, 2.0)
    assert (bis.code, bis.message) == ("E-ACT-002", "cannot read BIS/2025Q1.xlsx")
    assert by_step["load[TAL]"].state is S.SUCCEEDED
    assert by_step["load[TAL]"].finished_at is not None
    skipped = by_step["check[BIS]"]
    assert skipped.state is S.UPSTREAM_FAILED and skipped.started_at is None
    assert skipped.finished_at == 2.0
    assert by_step["consolidate"].state is S.SUCCEEDED
