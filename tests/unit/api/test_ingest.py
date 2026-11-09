"""POST /deployments/{id}/events: the core takes the history of a deployed service (US-31)."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx2
import pytest

from flowstat.deploy.package import build_bundle
from flowstat.repo.runs import RunStore
from flowstat.service.deployments import Running
from tests.unit.api.helpers import EXAMPLE, NAME, Env, T, make_env


class QuietLauncher:
    def __init__(self) -> None:
        self.envs: list[dict[str, str]] = []

    def start(self, bundle: Path, env: Mapping[str, str] | None = None) -> Running:
        self.envs.append(dict(env or {}))
        return Running("http://127.0.0.1:9100", f"pid-{len(self.envs)}")

    def stop(self, handle: str) -> None:
        pass


@pytest.fixture
def env(tmp_path: Path) -> Env:
    made = make_env(tmp_path, packager=build_bundle, launchers={"process": QuietLauncher()})
    made.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    made.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish")
    return made


def deploy(env: Env) -> tuple[str, str]:
    """A deployment and its ingest key."""
    made = env.call("admin", "POST", "/deployments", json={"workflow": NAME}).json()
    return made["id"], made["ingest_key"]


def event(seq: int, **more: Any) -> dict[str, Any]:
    return {"seq": seq, "kind": "STEP_STARTED", "at": float(seq), "step": "load"} | more


def batch(run: str = "s-1", n: int = 3, start: int = 0, version: int = 1) -> dict[str, Any]:
    return {
        "run": {
            "run_id": run,
            "workflow": NAME,
            "version": version,
            "inputs": {"quarter": "2025Q1"},
            "created_at": T,
        },
        "events": [event(i) for i in range(start, start + n)],
    }


def ship(env: Env, deployment: str, key: str | None, body: object) -> httpx2.Response:
    headers = {} if key is None else {"Authorization": f"Bearer {key}"}
    return env.client.post(f"/deployments/{deployment}/events", headers=headers, json=body)


def test_the_service_ships_a_run_and_the_core_shows_it(env: Env) -> None:
    deployment, key = deploy(env)
    response = ship(env, deployment, key, batch())
    assert response.status_code == 200
    assert response.json() == {"run_id": "s-1", "stored_through": 2}
    [run] = env.call("operator", "GET", f"/runs?deployment={deployment}").json()
    assert (run["run_id"], run["origin"], run["workflow"], run["version"]) == (
        "s-1",
        deployment,
        NAME,
        1,
    )
    row = env.call("operator", "GET", f"/deployments/{deployment}").json()
    assert (row["runs"], row["last_event_at"]) == (1, T)
    listing = env.call("operator", "GET", "/deployments").text
    assert key not in listing and "ingest_key" not in listing  # the key is shown once


def test_the_same_batch_twice_leaves_one_copy(env: Env) -> None:
    deployment, key = deploy(env)
    assert ship(env, deployment, key, batch()).status_code == 200
    assert ship(env, deployment, key, batch()).json()["stored_through"] == 2
    store = RunStore(sqlite3.connect(env.config.db_path))
    assert [e.seq for e in store.events("s-1")] == [0, 1, 2]


def test_a_later_batch_continues_the_run(env: Env) -> None:
    deployment, key = deploy(env)
    ship(env, deployment, key, batch(n=2))
    assert ship(env, deployment, key, batch(n=3, start=2)).json()["stored_through"] == 4


def test_a_gap_is_refused_and_says_what_the_core_needs(env: Env) -> None:
    deployment, key = deploy(env)
    ship(env, deployment, key, batch(n=2))
    response = ship(env, deployment, key, batch(n=2, start=5))
    assert response.status_code == 409
    [error] = response.json()["errors"]
    assert error["code"] == "E-API-003" and error["expected"] == 2


def test_a_gap_in_a_new_run_leaves_no_run_behind(env: Env) -> None:
    deployment, key = deploy(env)
    response = ship(env, deployment, key, batch(run="s-9", n=2, start=3))
    assert response.status_code == 409 and response.json()["errors"][0]["expected"] == 0
    assert env.call("operator", "GET", f"/runs?deployment={deployment}").json() == []
    assert env.call("operator", "GET", f"/deployments/{deployment}").json()["last_event_at"] is None


def test_a_different_event_under_a_used_number_is_a_conflict_without_expected(env: Env) -> None:
    deployment, key = deploy(env)
    ship(env, deployment, key, batch(n=2))
    other = batch(n=2)
    other["events"][1] = event(1, step="other")
    response = ship(env, deployment, key, other)
    assert response.status_code == 409
    assert "expected" not in response.json()["errors"][0]


def test_a_run_id_of_the_core_cannot_be_taken_by_a_service(env: Env) -> None:
    db = sqlite3.connect(env.config.db_path, isolation_level=None)
    RunStore(db).create("s-1", NAME, 1, {"quarter": "2025Q1"}, T)
    db.close()
    deployment, key = deploy(env)
    assert ship(env, deployment, key, batch()).status_code == 409


def test_the_workflow_and_the_version_must_be_the_ones_deployed(env: Env) -> None:
    deployment, key = deploy(env)
    response = ship(env, deployment, key, batch(version=2))
    assert response.status_code == 422 and response.json()["errors"][0]["code"] == "E-API-001"


def test_an_empty_batch_a_long_batch_and_a_malformed_one_are_422(env: Env) -> None:
    deployment, key = deploy(env)
    assert ship(env, deployment, key, batch(n=0)).status_code == 422
    assert ship(env, deployment, key, batch(n=201)).status_code == 422
    assert ship(env, deployment, key, batch(n=200)).status_code == 200
    assert ship(env, deployment, key, {"events": []}).status_code == 422
    bad = batch()
    bad["events"][0]["kind"] = "NOT_A_KIND"
    assert ship(env, deployment, key, bad).status_code == 422
    unknown = batch()
    unknown["events"][0]["colour"] = "red"
    assert ship(env, deployment, key, unknown).status_code == 422


# -- who may ship -----------------------------------------------------------------------------
def test_without_a_key_it_is_401_and_a_user_token_of_any_role_is_403(env: Env) -> None:
    deployment, _ = deploy(env)
    assert ship(env, deployment, None, batch()).status_code == 401
    for role in ("admin", "designer", "operator"):
        response = env.call(role, "POST", f"/deployments/{deployment}/events", json=batch())
        assert response.status_code == 403, role
        assert response.json()["errors"][0]["code"] == "E-AUTH-002"


def test_an_unknown_key_is_401(env: Env) -> None:
    deployment, _ = deploy(env)
    assert ship(env, deployment, "f" * 64, batch()).status_code == 401


def test_the_key_of_another_deployment_is_403_and_stores_nothing(env: Env) -> None:
    first, _ = deploy(env)
    _, second_key = deploy(env)
    response = ship(env, first, second_key, batch())
    assert response.status_code == 403 and response.json()["errors"][0]["code"] == "E-AUTH-002"
    assert env.call("operator", "GET", "/runs").json() == []


def test_a_key_for_a_deployment_that_does_not_exist_is_403(env: Env) -> None:
    _, key = deploy(env)
    assert ship(env, "none", key, batch()).status_code == 403


def test_the_ingest_key_opens_nothing_else(env: Env) -> None:
    _, key = deploy(env)
    headers = {"Authorization": f"Bearer {key}"}
    for method, path in (("GET", "/runs"), ("GET", "/deployments"), ("POST", "/deployments")):
        assert env.client.request(method, path, headers=headers).status_code == 401, path


def test_the_key_is_not_in_the_bundle_and_goes_to_the_service_by_environment(
    tmp_path: Path,
) -> None:
    launcher = QuietLauncher()
    made = make_env(tmp_path, packager=build_bundle, launchers={"process": launcher})
    # the core knows its own address only when the executable says so
    made.client.app.state.config = replace(  # type: ignore[attr-defined]
        made.config, ingest_url="http://127.0.0.1:8000"
    )
    made.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    made.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish")
    created = made.call("admin", "POST", "/deployments", json={"workflow": NAME}).json()
    [seen] = launcher.envs
    assert seen == {
        "FLOWSTAT_INGEST_URL": "http://127.0.0.1:8000",
        "FLOWSTAT_INGEST_KEY": created["ingest_key"],
        "FLOWSTAT_DEPLOYMENT_ID": created["id"],
    }
    for path in (made.config.bundles / created["id"]).rglob("*"):
        if path.is_file():
            assert created["ingest_key"] not in path.read_text(errors="ignore"), path
