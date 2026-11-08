"""Tests of the answer to an approval: the checks of api-spec section 5 and the driving of a run."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from pathlib import Path

import pytest

from flowstat.actions.registry import default_registry
from flowstat.compiler.catalogue import load_catalogue
from flowstat.dsl.reader import read_yaml
from flowstat.model.states import RunOutcome, StepState
from flowstat.repo.db import open_database
from flowstat.service.driving import Machine, drive
from flowstat.service.runs import (
    DecisionNotDeclaredError,
    Launch,
    RunService,
    StepNotFoundError,
    StepNotWaitingError,
    TitleMissingError,
)
from flowstat.service.workflows import WorkflowService
from tests.unit.actions.helpers import write_all_forms
from tests.unit.engine.helpers import ROOT

T = "2026-11-08T10:00:00Z"
NAME = "quarterly_consolidation"
TEXT = (ROOT / "examples" / "quarterly_consolidation.yaml").read_text(encoding="utf8")
CATALOGUE = (ROOT / "examples" / "catalogue.yaml").read_text(encoding="utf8")


def ticking() -> Callable[[], float]:
    counter = itertools.count(1)
    return lambda: float(next(counter))


class World:
    """A database with the example published, forms on disk, and a run that waits to be approved."""

    def __init__(self, tmp_path: Path) -> None:
        db = open_database(str(tmp_path / "flowstat.db"))
        registry = default_registry()
        self.workflows = WorkflowService(
            db, known_actions=registry.names(), signatures=registry.signatures()
        )
        tree, _ = read_yaml(CATALOGUE)
        assert tree is not None
        catalogue, _ = load_catalogue(tree)
        for list_name, items in catalogue.lists.items():
            self.workflows.set_catalogue_list(list_name, [dict(i) for i in items])
        self.workflows.save_draft(NAME, TEXT, T)
        self.workflows.publish(NAME, 1, T)
        self.runs = RunService(db, self.workflows)
        self.data = tmp_path / "data"
        write_all_forms(self.data, "2025Q1")
        self.machine = Machine(self.data, str(tmp_path / "target.db"), 2, ticking(), lambda s: None)

    def waiting(self, run_id: str = "r1") -> None:
        self.runs.start(run_id, NAME, 1, {"quarter": "2025Q1"}, T)
        report = drive(self.runs, run_id, self.runs.launch(run_id, 2), self.machine)
        assert report.status == "WAITING"

    def launch(self, run_id: str = "r1") -> Launch:
        return self.runs.recover(run_id, 2, 100.0, default_registry().is_idempotent)


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def test_a_run_that_reaches_the_approval_waits(world: World) -> None:
    world.waiting()
    assert world.runs.report("r1").status == "WAITING"


def test_the_decisions_of_an_approval_are_the_default_or_the_argument_of_the_step(
    world: World,
) -> None:
    runs = world.runs
    assert runs.declared_decisions("approval.wait", {"role": "x"}) == ("approved", "rejected")
    assert runs.declared_decisions("approval.wait", {"role": "x", "decisions": ["yes", "no"]}) == (
        "yes",
        "no",
    )
    assert runs.declared_decisions("stats.aggregate", {}) is None


def test_an_answer_with_the_title_finishes_the_run_and_records_who_gave_it(world: World) -> None:
    world.waiting()
    launch = world.launch()
    commands = world.runs.answer(
        launch, "approve", "approved", "olga", frozenset({"chief_statistician"}), 100.0
    )
    report = drive(world.runs, "r1", launch, world.machine, commands)
    assert report.status == "FINISHED" and report.outcome is RunOutcome.SUCCEEDED
    approve = next(s for s in report.steps if s.step == "approve")
    assert approve.state is StepState.SUCCEEDED
    assert approve.output == {"decision": "approved", "by": "olga"}


def test_an_answer_without_the_title_is_refused_and_changes_nothing(world: World) -> None:
    world.waiting()
    before = world.runs.store.events("r1")
    launch = world.launch()
    with pytest.raises(TitleMissingError) as info:
        world.runs.answer(launch, "approve", "approved", "dina", frozenset({"clerk"}), 100.0)
    assert info.value.title == "chief_statistician"
    assert world.runs.store.events("r1") == before


def test_an_admin_does_not_need_the_title(world: World) -> None:
    world.waiting()
    launch = world.launch()
    commands = world.runs.answer(launch, "approve", "approved", "ada", None, 100.0)
    assert drive(world.runs, "r1", launch, world.machine, commands).status == "FINISHED"


def test_a_decision_the_step_does_not_declare_is_refused(world: World) -> None:
    world.waiting()
    with pytest.raises(DecisionNotDeclaredError) as info:
        world.runs.answer(world.launch(), "approve", "maybe", "ada", None, 100.0)
    assert info.value.allowed == ("approved", "rejected")


def test_a_step_that_is_not_waiting_or_not_there_is_refused(world: World) -> None:
    world.waiting()
    with pytest.raises(StepNotWaitingError):
        world.runs.answer(world.launch(), "consolidate", "approved", "ada", None, 100.0)
    with pytest.raises(StepNotFoundError):
        world.runs.answer(world.launch(), "nothing", "approved", "ada", None, 100.0)


def test_the_order_of_the_checks_is_step_then_decision_then_title(world: World) -> None:
    world.waiting()
    launch = world.launch()
    with pytest.raises(DecisionNotDeclaredError):  # wrong decision and no title: the decision first
        world.runs.answer(launch, "approve", "maybe", "dina", frozenset(), 100.0)


def test_a_second_answer_after_the_run_finished_is_refused(world: World) -> None:
    world.waiting()
    launch = world.launch()
    commands = world.runs.answer(launch, "approve", "rejected", "ada", None, 100.0)
    drive(world.runs, "r1", launch, world.machine, commands)
    with pytest.raises(StepNotWaitingError):
        world.runs.answer(world.launch(), "approve", "approved", "ada", None, 200.0)
