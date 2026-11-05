"""Tests of the run service: start a run of a published version, store its history, report it."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from flowstat.compiler.plan import Plan
from flowstat.engine.scheduler import Start
from flowstat.engine.simulation import Done, Result, simulate
from flowstat.model.events import Event, EventKind
from flowstat.model.states import RunOutcome, StepState
from flowstat.repo.db import open_database
from flowstat.service.runs import InvalidInputsError, NotPublishedError, RunService
from flowstat.service.workflows import WorkflowService
from tests.unit.engine.helpers import ROOT

T = "2026-11-05T10:00:00Z"
CATALOGUE = (ROOT / "examples" / "catalogue.yaml").read_text(encoding="utf8")
TEXT = (ROOT / "examples" / "quarterly_consolidation.yaml").read_text(encoding="utf8")


def build(publish: bool = True) -> tuple[WorkflowService, RunService]:
    from flowstat.compiler.catalogue import load_catalogue
    from flowstat.dsl.reader import read_yaml

    db = open_database(":memory:")
    workflows = WorkflowService(db)
    tree, _ = read_yaml(CATALOGUE)
    assert tree is not None
    cat, _ = load_catalogue(tree)
    for list_name, items in cat.lists.items():
        workflows.set_catalogue_list(list_name, [dict(i) for i in items])
    saved = workflows.save_draft("quarterly_consolidation", TEXT, T)
    assert saved.diagnostics == []
    if publish:
        workflows.publish("quarterly_consolidation", saved.version.number, T)
    return workflows, RunService(db, workflows)


def simulated(plan: Plan, inputs: Mapping[str, object]) -> list[Event]:
    def act(command: Start) -> Result:
        return Done(1.0, {"rows": [1, 2], "written": 2, "decision": "approved"})

    return simulate(plan, inputs, 4, act).scheduler.events


def test_a_run_of_a_draft_is_refused() -> None:
    _, runs = build(publish=False)
    with pytest.raises(NotPublishedError):
        runs.start("r1", "quarterly_consolidation", 1, {"quarter": "2025Q1"}, T)


def test_a_run_with_wrong_inputs_is_refused_and_not_stored() -> None:
    _, runs = build()
    with pytest.raises(InvalidInputsError) as info:
        runs.start("r1", "quarterly_consolidation", 1, {"quarter": "nope"}, T)
    assert info.value.errors
    assert runs.list_runs() == []


def test_start_stores_the_run_with_the_resolved_inputs() -> None:
    _, runs = build()
    runs.start("r1", "quarterly_consolidation", 1, {"quarter": "2025Q1"}, T)
    stored = runs.store.get("r1")
    assert stored.workflow == "quarterly_consolidation" and stored.version == 1
    assert stored.inputs["quarter"] == "2025Q1"
    assert runs.report("r1").status == "CREATED"


def test_execute_stores_every_event_and_the_report_matches_the_scheduler() -> None:
    _, runs = build()
    report = runs.execute("r1", "quarterly_consolidation", 1, {"quarter": "2025Q1"}, T, simulated)
    assert report.status == "FINISHED" and report.outcome is RunOutcome.SUCCEEDED
    assert runs.store.events("r1")[0].kind is EventKind.RUN_STARTED
    plan = runs.workflows.get_plan("quarterly_consolidation", 1)

    def act(command: Start) -> Result:
        return Done(1.0, {"rows": [1, 2], "written": 2, "decision": "approved"})

    expected = simulate(plan, {"quarter": "2025Q1"}, 4, act).scheduler.states
    assert {s.step: s.state for s in report.steps} == expected
    assert StepState.SUCCEEDED in expected.values()
    assert report.workflow == "quarterly_consolidation" and report.version == 1
    assert len(report.steps) == len(runs.workflows.get_plan("quarterly_consolidation", 1).steps)


def test_the_report_of_a_stored_run_is_the_same_after_reading_it_again() -> None:
    _, runs = build()
    runs.execute("r1", "quarterly_consolidation", 1, {"quarter": "2025Q1"}, T, simulated)
    assert runs.report("r1") == runs.report("r1")


def test_two_runs_are_kept_apart() -> None:
    _, runs = build()
    runs.execute("r1", "quarterly_consolidation", 1, {"quarter": "2025Q1"}, T, simulated)
    runs.execute("r2", "quarterly_consolidation", 1, {"quarter": "2025Q2"}, T, simulated)
    assert [r.run_id for r in runs.list_runs("quarterly_consolidation")] == ["r1", "r2"]
    assert runs.store.get("r2").inputs["quarter"] == "2025Q2"
    assert len(runs.store.events("r1")) == len(runs.store.events("r2"))


def test_a_report_of_an_unknown_run_is_refused() -> None:
    from flowstat.repo.runs import RunNotFoundError

    _, runs = build()
    with pytest.raises(RunNotFoundError):
        runs.report("nope")


# -- the order of ready steps follows the feasible critical path (US-35) ----------------------
GUARDED = """\
workflow: guarded
version: 1
inputs:
  amount: {type: int, min: 0, max: 100}
steps:
  p:
    action: x.y
  p2:
    action: x.y
    needs: [p]
    when: "${inputs.amount} > 50"
  p3:
    action: x.y
    needs: [p2]
  q:
    action: x.y
  q2:
    action: x.y
    needs: [q]
goals: {all_of: [p3, q2]}
"""


def first_started(amount: int) -> str:
    db = open_database(":memory:")
    workflows = WorkflowService(db)
    saved = workflows.save_draft("guarded", GUARDED, T)
    assert saved.diagnostics == []
    workflows.publish("guarded", saved.version.number, T)
    runs = RunService(db, workflows)
    runs.start("r1", "guarded", 1, {"amount": amount}, T)
    [first] = runs.launch("r1", 1).scheduler.start(0.0)
    assert isinstance(first, Start)
    return first.step


def test_a_run_starts_first_the_step_with_the_longest_chain_that_can_run() -> None:
    # the chain behind p is three long, the one behind q two; but p2 runs only for amount > 50
    assert first_started(80) == "p"
    assert first_started(10) == "q"
