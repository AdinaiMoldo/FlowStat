"""The plan cache as the core uses it: reports and listings stop compiling again (US-41)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import flowstat.service.workflows as module
from flowstat.api.app import create_app
from flowstat.compiler.cache import PlanCache
from tests.unit.actions.helpers import write_all_forms
from tests.unit.api.helpers import EXAMPLE, NAME, Env, make_env


@pytest.fixture
def compiles(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    real = module.compile_workflow  # type: ignore[attr-defined]

    def counting(text, *args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(text)
        return real(text, *args, **kwargs)

    monkeypatch.setattr(module, "compile_workflow", counting)
    return seen


def started(tmp_path: Path, runs: int = 5) -> Env:
    env = make_env(tmp_path)
    env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish")
    write_all_forms(env.config.data_dir, "2025Q1")
    for _ in range(runs):
        env.call(
            "operator", "POST", f"/workflows/{NAME}/runs", json={"inputs": {"quarter": "2025Q1"}}
        )
    return env


def test_reading_many_reports_compiles_the_plan_once(tmp_path: Path, compiles: list[str]) -> None:
    env = started(tmp_path)
    compiles.clear()
    env.client.app.state.registry.plans = PlanCache(8)  # type: ignore[attr-defined]  # a cold cache
    for n in range(1, 6):
        assert env.call("operator", "GET", f"/runs/r-{n:04d}").status_code == 200
    assert len(compiles) == 1


def test_listing_runs_does_not_compile_a_plan_for_each_run(
    tmp_path: Path, compiles: list[str]
) -> None:
    env = started(tmp_path)
    compiles.clear()
    env.client.app.state.registry.plans = PlanCache(8)  # type: ignore[attr-defined]
    assert len(env.call("operator", "GET", "/runs").json()) == 5
    assert len(compiles) <= 1


def test_the_report_with_the_cache_is_the_report_without_it(tmp_path: Path) -> None:
    env = started(tmp_path)
    warm = [env.call("operator", "GET", f"/runs/r-{n:04d}").json() for n in range(1, 6)]
    cold_app = create_app(replace(env.config))
    cold_app.state.registry.plans = PlanCache(0)
    cold = TestClient(cold_app)
    headers = {"Authorization": "Bearer " + ("o3" * 32)}
    same: list[Any] = [cold.get(f"/runs/r-{n:04d}", headers=headers).json() for n in range(1, 6)]
    assert same == warm


def test_a_new_version_gets_its_own_plan_and_the_old_one_stays(
    tmp_path: Path, compiles: list[str]
) -> None:
    env = started(tmp_path, runs=1)
    second = EXAMPLE.replace("version: 1", "version: 2", 1)
    env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": second})
    assert env.call("designer", "POST", f"/workflows/{NAME}/versions/2/publish").status_code == 200
    compiles.clear()
    env.client.app.state.registry.plans = PlanCache(8)  # type: ignore[attr-defined]
    env.call(
        "operator",
        "POST",
        f"/workflows/{NAME}/runs",
        json={"inputs": {"quarter": "2025Q1"}, "version": 2},
    )
    env.call("operator", "GET", "/runs/r-0001")
    env.call("operator", "GET", "/runs/r-0002")
    env.call("operator", "GET", "/runs/r-0001")
    assert len(compiles) == 2  # one for each version, however many reports
