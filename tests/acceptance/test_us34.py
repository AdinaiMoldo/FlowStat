"""US-34. Given 10 runs with 2 failures, when I open statistics, then the failure rate shows 20%."""

from __future__ import annotations

from pathlib import Path

from flowstat.model.events import Event, EventKind
from flowstat.model.states import RunOutcome
from flowstat.repo.db import connect
from flowstat.repo.runs import RunStore
from tests.unit.api.helpers import Env, make_env

T = "2026-11-09T10:00:00Z"


def seed(env: Env, outcomes: list[RunOutcome], workflow: str = "demo", version: int = 1) -> None:
    db = connect(env.config.db_path)
    store = RunStore(db)
    base = len(store.runs())
    for i, outcome in enumerate(outcomes, start=base):
        store.create(f"s{i:03d}", workflow, version, {}, T)
        store.append(
            f"s{i:03d}",
            [
                Event(0, EventKind.RUN_STARTED, 100.0 * i),
                Event(1, EventKind.RUN_FINISHED, 100.0 * i + 40.0, outcome=outcome),
            ],
        )
    db.close()


def test_ten_runs_with_two_failures_show_a_failure_rate_of_twenty_percent(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    seed(env, [RunOutcome.SUCCEEDED] * 7 + [RunOutcome.GOAL_NOT_REACHED, RunOutcome.FAILED] * 1)
    seed(env, [RunOutcome.COMPENSATION_FAILED])
    response = env.call("admin", "GET", "/statistics")
    assert response.status_code == 200, response.text
    [entry] = response.json()["workflows"]
    assert entry == {
        "workflow": "demo",
        "runs": 10,
        "running": 0,
        "finished": 10,
        "succeeded": 7,
        "not_reached": 1,
        "cancelled": 0,
        "failed": 2,
        "failure_rate": 0.2,
        "average_duration": 40.0,
        "versions": {"1": {"runs": 10, "failed": 2}},
    }


def test_a_workflow_with_no_finished_run_has_no_rate(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    db = connect(env.config.db_path)
    RunStore(db).create("r1", "demo", 2, {}, T)
    db.close()
    [entry] = env.call("admin", "GET", "/statistics").json()["workflows"]
    assert entry["runs"] == 1 and entry["running"] == 1
    assert entry["failure_rate"] is None and entry["average_duration"] is None
    assert entry["versions"] == {"2": {"runs": 1, "failed": 0}}


def test_no_runs_give_an_empty_list(tmp_path: Path) -> None:
    assert make_env(tmp_path).call("admin", "GET", "/statistics").json() == {"workflows": []}


def test_only_an_admin_may_read_the_statistics(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    assert env.call(None, "GET", "/statistics").status_code == 401
    for role in ("designer", "operator", "clerk"):
        assert env.call(role, "GET", "/statistics").status_code == 403
