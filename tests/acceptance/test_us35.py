"""Acceptance test of US-35: steps on the longest chain that can really run start first.

Given a chain of steps whose guards exclude each other, and a shorter chain that can run, when one
worker is free, then the step that opens the shorter chain starts first. The plain longest path
would start the other.
"""

from __future__ import annotations

from pathlib import Path

from flowstat.actions.registry import default_registry
from flowstat.compiler.catalogue import Catalogue
from flowstat.compiler.compile import compile_workflow
from flowstat.engine.scheduler import Scheduler, Start
from flowstat.repo.db import open_database
from flowstat.service.runs import RunService
from flowstat.service.workflows import WorkflowService

T = "2026-11-05T10:00:00Z"
ROOT = Path(__file__).resolve().parents[2]

# a opens a chain of five, but x1 and x2 need amount > 50 and y1 and y2 need amount <= 50, so the
# longest chain that can run behind a has three steps; b opens a chain of four that can
WORKFLOW = """\
workflow: exclusive
version: 1
inputs:
  amount: {type: int, min: 0, max: 100}
steps:
  a:
    action: x.y
  b:
    action: x.y
  x1:
    action: x.y
    needs: [a]
    when: "${inputs.amount} > 50"
  x2:
    action: x.y
    needs: [x1]
    when: "${inputs.amount} > 50"
  y1:
    action: x.y
    needs: [x2]
    join: all_done
    when: "${inputs.amount} <= 50"
  y2:
    action: x.y
    needs: [y1]
    when: "${inputs.amount} <= 50"
  b2:
    action: x.y
    needs: [b]
  b3:
    action: x.y
    needs: [b2]
  b4:
    action: x.y
    needs: [b3]
goals: {all_of: [y2, b4]}
"""


def runs() -> RunService:
    db = open_database(":memory:")
    workflows = WorkflowService(db)
    saved = workflows.save_draft("exclusive", WORKFLOW, T)
    workflows.publish("exclusive", saved.version.number, T)
    return RunService(db, workflows)


def first(service: RunService, run_id: str) -> str:
    [command] = service.launch(run_id, 1).scheduler.start(0.0)
    assert isinstance(command, Start)
    return command.step


def test_the_step_that_opens_the_shorter_chain_that_can_run_starts_first() -> None:
    service = runs()
    service.start("r1", "exclusive", 1, {"amount": 70}, T)
    assert first(service, "r1") == "b"  # a leads to 3 steps that can run, b to 4
    # the plain longest path counts the five steps behind a, the two groups that exclude each other
    result = compile_workflow(WORKFLOW, Catalogue({}))
    assert result.plan is not None
    [plain] = Scheduler(result.plan, {"amount": 70}, 1).start(0.0)
    assert isinstance(plain, Start) and plain.step == "a"


def test_the_order_is_the_same_for_the_other_side_of_the_guards() -> None:
    service = runs()
    service.start("r1", "exclusive", 1, {"amount": 20}, T)
    assert first(service, "r1") == "b"


def test_the_example_workflow_is_prioritised_without_trouble() -> None:
    from flowstat.analysis.critical import priorities

    text = (ROOT / "examples" / "quarterly_consolidation.yaml").read_text(encoding="utf8")
    regions = tuple({"code": c, "name": c} for c in "ABCDEFGHI")
    plan = compile_workflow(text, Catalogue({"regions": regions})).plan
    assert plan is not None
    got = priorities(plan, default_registry().signatures(), inputs={"quarter": "2025Q1"})
    assert not got.cut
    # a load opens: load, check, consolidate, approve, publish (or return) = 5 steps
    assert got.priority["load[A]"] == 5.0
    assert got.priority["approve"] == 2.0
