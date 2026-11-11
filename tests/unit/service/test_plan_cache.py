"""The plan of a published version is compiled once for a process (US-41)."""

from __future__ import annotations

from pathlib import Path

import pytest

import flowstat.service.workflows as module
from flowstat.actions.registry import default_registry
from flowstat.compiler.cache import PlanCache
from flowstat.compiler.catalogue import load_catalogue
from flowstat.dsl.reader import read_yaml
from flowstat.repo.db import open_database
from flowstat.service.workflows import InvalidWorkflowError, WorkflowService
from tests.unit.engine.helpers import ROOT

T = "2026-11-08T10:00:00Z"
TEXT = (ROOT / "examples" / "quarterly_consolidation.yaml").read_text(encoding="utf8")
NAME = "quarterly_consolidation"


@pytest.fixture
def compiles(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The texts the compiler was given, in order."""
    seen: list[str] = []
    real = module.compile_workflow  # type: ignore[attr-defined]

    def counting(text, *args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(text)
        return real(text, *args, **kwargs)

    monkeypatch.setattr(module, "compile_workflow", counting)
    return seen


def service(tmp_path: Path, plans: PlanCache | None, publish: bool = True) -> WorkflowService:  # type: ignore[type-arg]
    db = open_database(str(tmp_path / "f.db"))
    registry = default_registry()
    workflows = WorkflowService(
        db, known_actions=registry.names(), signatures=registry.signatures(), plans=plans
    )
    tree, _ = read_yaml((ROOT / "examples" / "catalogue.yaml").read_text(encoding="utf8"))
    assert tree is not None
    catalogue, _ = load_catalogue(tree)
    for name, items in catalogue.lists.items():
        workflows.set_catalogue_list(name, [dict(i) for i in items])
    workflows.save_draft(NAME, TEXT, T)
    if publish:
        workflows.publish(NAME, 1, T)
    return workflows


def test_a_published_version_is_compiled_once_however_often_its_plan_is_asked_for(
    tmp_path: Path, compiles: list[str]
) -> None:
    workflows = service(tmp_path, PlanCache(8))
    before = len(compiles)
    plans = [workflows.get_plan(NAME, 1) for _ in range(50)]
    assert len(compiles) - before == 1
    assert all(p is plans[0] for p in plans)


def test_without_a_cache_it_is_compiled_every_time(tmp_path: Path, compiles: list[str]) -> None:
    workflows = service(tmp_path, None)
    before = len(compiles)
    for _ in range(5):
        workflows.get_plan(NAME, 1)
    assert len(compiles) - before == 5


def test_a_draft_is_never_kept_because_its_text_may_change(
    tmp_path: Path, compiles: list[str]
) -> None:
    cache: PlanCache = PlanCache(8)  # type: ignore[type-arg]
    workflows = service(tmp_path, cache, publish=False)
    before = len(compiles)
    workflows.get_plan(NAME, 1)
    workflows.get_plan(NAME, 1)
    assert len(compiles) - before == 2 and len(cache) == 0


def test_the_plan_of_the_cache_is_the_plan_of_a_compile(tmp_path: Path) -> None:
    cached = service(tmp_path, PlanCache(8)).get_plan(NAME, 1)
    uncached = WorkflowService(
        open_database(str(tmp_path / "f.db")),
        known_actions=default_registry().names(),
        signatures=default_registry().signatures(),
    ).get_plan(NAME, 1)
    assert cached == uncached


def test_a_text_that_does_not_compile_is_never_kept(tmp_path: Path) -> None:
    cache: PlanCache = PlanCache(8)  # type: ignore[type-arg]
    workflows = service(tmp_path, cache, publish=False)
    workflows.save_draft(NAME, "version: 1\nworkflow: x\nsteps: [", T)
    for _ in range(2):
        with pytest.raises(InvalidWorkflowError):
            workflows.get_plan(NAME, 1)
    assert len(cache) == 0


def test_two_processes_with_their_own_caches_do_not_share_plans(
    tmp_path: Path, compiles: list[str]
) -> None:
    first, second = PlanCache(8), PlanCache(8)  # type: ignore[var-annotated]
    one = service(tmp_path, first)
    before = len(compiles)
    one.get_plan(NAME, 1)
    WorkflowService(
        open_database(str(tmp_path / "f.db")),
        known_actions=default_registry().names(),
        signatures=default_registry().signatures(),
        plans=second,
    ).get_plan(NAME, 1)
    assert len(compiles) - before == 2 and len(first) == len(second) == 1
