"""The health of the services, as the core shows it (US-37, docs/api-spec.md section 7)."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from flowstat.api.app import create_app
from flowstat.api.probing import probe_due, start_probing
from flowstat.deploy.package import build_bundle
from tests.unit.actions.helpers import write_all_forms
from tests.unit.api.helpers import EXAMPLE, NAME, Env, make_env
from tests.unit.api.test_deployments import FakeLauncher


class Health:
    def __init__(self) -> None:
        self.up = True
        self.asked: list[str] = []

    def __call__(self, endpoint: str) -> bool:
        self.asked.append(endpoint)
        return self.up


def environment(tmp_path: Path) -> tuple[Env, Health, FakeLauncher]:
    launcher, health = FakeLauncher(), Health()
    base = make_env(tmp_path, packager=build_bundle, launchers={"process": launcher})
    config = replace(base.config, health=health)
    env = Env(TestClient(create_app(config)), config)
    env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish")
    assert env.call("admin", "POST", "/deployments", json={"workflow": NAME}).status_code == 201
    return env, health, launcher


def row(env: Env) -> dict[str, Any]:
    body: dict[str, Any] = env.call("admin", "GET", "/deployments/r-0001").json()
    return body


def test_a_deployment_that_started_is_running_and_answering(tmp_path: Path) -> None:
    env, _, _ = environment(tmp_path)
    assert row(env)["status"] == "running" and row(env)["answering"] is True


def test_a_service_that_stops_answering_is_listed_unreachable_and_comes_back(
    tmp_path: Path,
) -> None:
    env, health, _ = environment(tmp_path)
    health.up = False
    assert probe_due(env.config) == ["r-0001"]
    shown = row(env)
    assert shown["status"] == "unreachable" and shown["answering"] is False
    assert "does not answer" in shown["detail"] and shown["answering_since"]
    [listed] = env.call("operator", "GET", "/deployments").json()
    assert listed["status"] == "unreachable"
    health.up = True
    assert probe_due(env.config) == ["r-0001"]
    assert row(env)["status"] == "running" and row(env)["answering"] is True
    assert not row(env)["detail"]


def test_a_probe_with_nothing_new_returns_nothing(tmp_path: Path) -> None:
    env, _, _ = environment(tmp_path)
    assert probe_due(env.config) == []


def test_a_service_stopped_on_purpose_is_not_probed(tmp_path: Path) -> None:
    env, health, launcher = environment(tmp_path)
    assert env.call("admin", "DELETE", "/deployments/r-0001").status_code == 200
    health.up, health.asked = False, []
    assert probe_due(env.config) == [] and health.asked == []
    assert row(env)["status"] == "stopped" and launcher.stopped == ["pid-1"]


def test_an_unreachable_service_may_still_be_stopped(tmp_path: Path) -> None:
    env, health, launcher = environment(tmp_path)
    health.up = False
    probe_due(env.config)
    stopped = env.call("admin", "DELETE", "/deployments/r-0001")
    assert stopped.status_code == 200 and stopped.json()["status"] == "stopped"
    assert launcher.stopped == ["pid-1"]


def test_runs_cannot_be_moved_to_a_service_that_does_not_answer(tmp_path: Path) -> None:
    env, health, _ = environment(tmp_path)
    second_text = EXAMPLE.replace("version: 1", "version: 2", 1)
    env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": second_text})
    assert env.call("designer", "POST", f"/workflows/{NAME}/versions/2/publish").status_code == 200
    second = env.call("admin", "POST", "/deployments", json={"workflow": NAME, "version": 2})
    assert second.status_code == 201, second.text
    target = second.json()["id"]
    write_all_forms(env.config.data_dir, "2025Q1")
    started = env.call(
        "operator",
        "POST",
        f"/workflows/{NAME}/runs",
        json={"inputs": {"quarter": "2025Q1"}, "version": 1},
    )
    run = started.json()["run_id"]
    health.up = False
    probe_due(env.config)
    refused = env.call("admin", "POST", f"/runs/{run}/migrate", json={"to": 2, "owner": target})
    assert refused.status_code == 409
    assert "unreachable, not running" in refused.json()["errors"][0]["message"]


def test_a_core_without_a_health_check_probes_nothing(tmp_path: Path) -> None:
    env, _, _ = environment(tmp_path)
    assert probe_due(replace(env.config, health=None)) == []
    assert row(env)["status"] == "running"


def test_a_probe_that_fails_is_logged_and_does_not_stop_the_core(tmp_path: Path) -> None:
    env, _, _ = environment(tmp_path)

    def broken(endpoint: str) -> bool:
        raise RuntimeError("the network is gone")

    assert probe_due(replace(env.config, health=broken)) == []
    assert row(env)["status"] == "running"  # a failing probe is not an answer


def test_the_probe_thread_marks_the_service_by_itself(tmp_path: Path) -> None:
    env, health, _ = environment(tmp_path)
    health.up = False
    ticker = start_probing(env.client.app, env.config, 0.01)  # type: ignore[arg-type]
    assert ticker is not None
    try:
        for _ in range(500):
            if row(env)["status"] == "unreachable":
                break
            time.sleep(0.01)
    finally:
        ticker.stop(5.0)
    assert row(env)["status"] == "unreachable"


def test_no_interval_means_no_thread(tmp_path: Path) -> None:
    env, _, _ = environment(tmp_path)
    assert start_probing(env.client.app, env.config, 0) is None  # type: ignore[arg-type]
