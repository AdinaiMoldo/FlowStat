"""Tests of the deployment routes with the real packager and a launcher that starts nothing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx2
import pytest

from flowstat.deploy.package import build_bundle
from flowstat.service.bundle import read_bundle
from flowstat.service.deployments import LaunchError, Running
from flowstat.service.users import hash_token
from tests.unit.api.helpers import EXAMPLE, NAME, Env, make_env


class FakeLauncher:
    def __init__(self) -> None:
        self.started: list[Path] = []
        self.stopped: list[str] = []
        self.fail: str | None = None

    def start(self, bundle: Path) -> Running:
        if self.fail:
            raise LaunchError("did not start", self.fail)
        self.started.append(bundle)
        return Running("http://127.0.0.1:9100", "pid-1")

    def stop(self, handle: str) -> None:
        self.stopped.append(handle)


@pytest.fixture
def launcher() -> FakeLauncher:
    return FakeLauncher()


@pytest.fixture
def env(tmp_path: Path, launcher: FakeLauncher) -> Env:
    made = make_env(tmp_path, packager=build_bundle, launchers={"process": launcher})
    made.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    made.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish")
    return made


def body(response: httpx2.Response) -> Any:
    return response.json()


def deploy(env: Env, role: str = "admin", **extra: Any) -> httpx2.Response:
    return env.call(role, "POST", "/deployments", json={"workflow": NAME} | extra)


def test_an_admin_deploys_the_latest_published_version_and_gets_the_key_once(
    env: Env, launcher: FakeLauncher
) -> None:
    response = deploy(env)
    assert response.status_code == 201
    made = body(response)
    assert made["id"] == "r-0001" and made["status"] == "running" and made["kind"] == "process"
    assert made["endpoint"] == "http://127.0.0.1:9100" and made["version"] == 1
    assert made["api_key"] == "k" * 64
    # the key is shown here and nowhere else; the bundle keeps its hash only
    assert launcher.started == [env.config.bundles / "r-0001"]
    manifest = read_bundle(env.config.bundles / "r-0001").manifest
    assert manifest.api_key_sha256 == hash_token("k" * 64)
    assert "k" * 64 not in (env.config.bundles / "r-0001" / "manifest.json").read_text()
    for path in ("/deployments", "/deployments/r-0001"):
        assert "api_key" not in env.call("operator", "GET", path).text


def test_only_an_admin_deploys_or_stops_and_everyone_may_look(env: Env) -> None:
    assert deploy(env, "designer").status_code == 403
    assert deploy(env, "operator").status_code == 403
    deploy(env)
    assert env.call("designer", "DELETE", "/deployments/r-0001").status_code == 403
    for role in ("admin", "designer", "operator"):
        assert env.call(role, "GET", "/deployments").status_code == 200


def test_the_list_and_one_deployment(env: Env) -> None:
    deploy(env)
    [row] = body(env.call("operator", "GET", "/deployments"))
    assert row["id"] == "r-0001" and row["status"] == "running"
    assert body(env.call("operator", "GET", "/deployments/r-0001")) == row
    assert env.call("operator", "GET", "/deployments/none").status_code == 404


def test_a_draft_an_unknown_workflow_an_unknown_kind_and_a_missing_launcher_are_refused(
    env: Env, launcher: FakeLauncher
) -> None:
    env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})  # v2 draft
    draft = deploy(env, version=2)
    assert draft.status_code == 409 and body(draft)["errors"][0]["code"] == "E-API-003"
    assert deploy(env, version=9).status_code == 404
    assert env.call("admin", "POST", "/deployments", json={"workflow": "none"}).status_code == 404
    kind = deploy(env, kind="vm")
    assert kind.status_code == 422 and body(kind)["errors"][0]["field"] == "kind"
    docker = deploy(env, kind="docker")
    assert docker.status_code == 409 and "not available" in body(docker)["errors"][0]["message"]
    assert launcher.started == [] and body(env.call("admin", "GET", "/deployments")) == []


def test_a_service_that_does_not_start_is_409_and_stays_in_the_list_as_failed(
    env: Env, launcher: FakeLauncher
) -> None:
    launcher.fail = "port 9100 is taken"
    response = deploy(env)
    assert response.status_code == 409
    assert "port 9100 is taken" in body(response)["errors"][0]["message"]
    [row] = body(env.call("admin", "GET", "/deployments"))
    assert row["status"] == "failed" and row["detail"] == "port 9100 is taken"
    assert env.call("admin", "DELETE", f"/deployments/{row['id']}").status_code == 409


def test_a_running_deployment_is_stopped_once(env: Env, launcher: FakeLauncher) -> None:
    deploy(env)
    stopped = env.call("admin", "DELETE", "/deployments/r-0001")
    assert stopped.status_code == 200 and body(stopped)["status"] == "stopped"
    assert launcher.stopped == ["pid-1"]
    assert env.call("admin", "DELETE", "/deployments/r-0001").status_code == 409
    assert env.call("admin", "DELETE", "/deployments/none").status_code == 404
