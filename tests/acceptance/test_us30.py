"""Acceptance test of US-30: an admin deploys a workflow as a separate service.

Given v4 is published, when I deploy it, then the service answers on its endpoint and returns the
same results as the core's executor for the same inputs. "The same results" (api-spec section 7):
the final state, error code and output of every step, and the outcome of the run; times and run
ids may differ.
"""

from __future__ import annotations

import functools
import os
import subprocess
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient

from flowstat.api.config import AppConfig
from flowstat.api.service import create_service_app
from flowstat.deploy.launchers import ProcessLauncher
from flowstat.deploy.package import build_bundle
from flowstat.repo.db import connect
from flowstat.service.bundle import read_bundle
from flowstat.service.deployments import Launcher, Running
from flowstat.service.workflows import WorkflowService
from tests.unit.actions.helpers import write_all_forms
from tests.unit.api.helpers import EXAMPLE, NAME, Env, make_env

ROOT = Path(__file__).resolve().parents[2]
QUARTER = {"inputs": {"quarter": "2025Q1"}}


def publish_four(env: Env) -> None:
    """Versions 1 to 3 are published first, so that the deployed one is v4 as in the story."""
    for number in range(1, 5):
        text = EXAMPLE.replace("version: 1", f"version: {number}", 1)
        saved = env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": text})
        assert saved.json()["version"] == number
        published = env.call("designer", "POST", f"/workflows/{NAME}/versions/{number}/publish")
        assert published.status_code == 200


class InProcessLauncher:
    """Starts the service app of the bundle inside this test instead of in a process."""

    def __init__(self, submit: Any) -> None:
        self.apps: dict[str, TestClient] = {}
        self.submit = submit

    def start(self, bundle: Path) -> Running:
        data = read_bundle(bundle)
        config = AppConfig(
            db_path=str(bundle / "service.db"),
            data_dir=bundle / "data",
            target=str(bundle / "target.db"),
            clock=lambda: "2026-11-08T10:00:00Z",
            time=_ticks(),
            sleep=lambda s: None,
            new_id=_ids("sr"),
            new_token=lambda: "unused",
            submit=self.submit,
            bundles=bundle,
            workers=2,
        )
        self.apps[str(bundle)] = TestClient(create_service_app(config, data))
        return Running(f"in-process:{bundle.name}", str(bundle))

    def stop(self, handle: str) -> None:
        self.apps.pop(handle, None)


def _ticks() -> Any:
    import itertools

    counter = itertools.count(1)
    return lambda: float(next(counter))


def _ids(prefix: str) -> Any:
    import itertools

    counter = itertools.count(1)
    return lambda: f"{prefix}-{next(counter):04d}"


def normalised(report: dict[str, Any]) -> dict[str, Any]:
    """What must be equal between the core and a service: no times, no run ids, and not who
    answered an approval (`by` is a person in the core and the key of the service in a service)."""
    text = str(report["run_id"])
    steps = {}
    for step in report["steps"]:
        steps[step["step"]] = (
            step["state"],
            step["code"],
            step["attempts"],
            _without(step["output"], text),
        )
        if step["step"] == "approve":
            output = dict(step["output"] or {})
            if step["state"] == "SUCCEEDED":
                assert output.pop("by")  # somebody is named, and it is not compared
            steps[step["step"]] = (*steps[step["step"]][:3], output)
    return {
        "workflow": report["workflow"],
        "version": report["version"],
        "status": report["status"],
        "outcome": report["outcome"],
        "steps": steps,
    }


def _without(value: Any, run_id: str) -> Any:
    if isinstance(value, dict):
        return {k: _without(v, run_id) for k, v in value.items()}
    if isinstance(value, list):
        return [_without(v, run_id) for v in value]
    if isinstance(value, str):
        return value.replace(run_id, "<run>")
    return value


class World:
    def __init__(self, tmp_path: Path) -> None:
        self.launcher = InProcessLauncher(lambda job: job())
        self.env = make_env(tmp_path, packager=build_bundle, launchers={"process": self.launcher})
        publish_four(self.env)
        write_all_forms(self.env.config.data_dir, "2025Q1")

    def deploy(self) -> tuple[dict[str, Any], TestClient, dict[str, str]]:
        response = self.env.call(
            "admin", "POST", "/deployments", json={"workflow": NAME, "version": 4}
        )
        assert response.status_code == 201, response.text
        made = response.json()
        client = self.launcher.apps[str(self.env.config.bundles / made["id"])]
        return made, client, {"Authorization": f"Bearer {made['api_key']}"}


def forms(world: World, made: dict[str, Any], missing: tuple[str, ...]) -> None:
    for root in (world.env.config.data_dir, world.env.config.bundles / made["id"] / "data"):
        for path in root.glob("*/2025Q1.xlsx"):
            path.unlink()
        write_all_forms(root, "2025Q1", missing=missing)


@pytest.mark.parametrize(
    ("decision", "missing", "outcome"),
    [
        ("approved", (), "SUCCEEDED"),
        ("rejected", (), "SUCCEEDED"),  # the publication is skipped, the goal is still reached
        ("approved", ("BIS", "OSC"), "SUCCEEDED"),  # two regions never sent their form
        ("approved", ("BIS", "OSC", "BAT", "JAL", "ISK"), "GOAL_NOT_REACHED"),  # below the quorum
    ],
)
def test_the_service_returns_the_same_results_as_the_core(
    tmp_path: Path, decision: str, missing: tuple[str, ...], outcome: str
) -> None:
    world = World(tmp_path)
    made, service, key = world.deploy()
    forms(world, made, missing)

    core = world.env
    started = core.call("operator", "POST", f"/workflows/{NAME}/runs", json=QUARTER).json()
    in_service = service.post("/runs", json=QUARTER, headers=key).json()
    core_id, service_id = started["run_id"], in_service["run_id"]
    assert started["version"] == in_service["version"] == 4

    answer = {"step": "approve", "decision": decision}
    core_report = core.call("operator", "GET", f"/runs/{core_id}").json()
    if core_report["status"] == "WAITING":
        core.call("operator", "POST", f"/runs/{core_id}/decisions", json=answer)
        service.post(f"/runs/{service_id}/decisions", json=answer, headers=key)
    core_report = core.call("operator", "GET", f"/runs/{core_id}").json()
    service_report = service.get(f"/runs/{service_id}", headers=key).json()

    assert normalised(service_report) == normalised(core_report)
    assert core_report["status"] == "FINISHED"
    assert core_report["outcome"] == outcome  # both could be wrong in the same way, so it is named


def test_invalid_inputs_are_refused_by_the_service_as_by_the_core(tmp_path: Path) -> None:
    world = World(tmp_path)
    _, service, key = world.deploy()
    bad = {"inputs": {"quarter": "Q9"}}
    core = world.env.call("operator", "POST", f"/workflows/{NAME}/runs", json=bad)
    here = service.post("/runs", json=bad, headers=key)
    assert core.status_code == here.status_code == 422
    assert core.json() == here.json()


def test_the_service_compiles_to_the_plan_the_core_runs(tmp_path: Path) -> None:
    world = World(tmp_path)
    made, _, _ = world.deploy()
    core_db = connect(world.env.config.db_path)
    service_db = connect(str(world.env.config.bundles / made["id"] / "service.db"))
    try:
        core_plan = WorkflowService(core_db).get_plan(NAME, 4)
        service_plan = WorkflowService(service_db).get_plan(NAME, 4)
    finally:
        core_db.close()
        service_db.close()
    assert core_plan == service_plan and len(core_plan.steps) > 10


def test_the_frozen_text_is_the_published_text_byte_for_byte(tmp_path: Path) -> None:
    world = World(tmp_path)
    made, _, _ = world.deploy()
    frozen = (world.env.config.bundles / made["id"] / "workflow.yaml").read_bytes()
    stored = world.env.call("operator", "GET", f"/workflows/{NAME}/versions/4").json()["text"]
    assert frozen == stored.encode("utf8")


# -- a real process ---------------------------------------------------------------------------
def test_a_service_started_as_a_process_answers_and_matches_the_core(tmp_path: Path) -> None:
    """The path that is tested everywhere (the Proposal's fallback): uvicorn on 127.0.0.1."""
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src")])}
    process: Launcher = ProcessLauncher(
        popen=functools.partial(subprocess.Popen, env=environment), limit=60.0
    )
    env = make_env(tmp_path, packager=build_bundle, launchers={"process": process})
    publish_four(env)
    write_all_forms(env.config.data_dir, "2025Q1")

    made = env.call(
        "admin", "POST", "/deployments", json={"workflow": NAME, "version": 4, "kind": "process"}
    )
    assert made.status_code == 201, made.text
    row = made.json()
    try:
        assert row["status"] == "running" and row["endpoint"].startswith("http://127.0.0.1:")
        write_all_forms(env.config.bundles / row["id"] / "data", "2025Q1")
        key = {"Authorization": f"Bearer {row['api_key']}"}
        health = httpx2.get(row["endpoint"] + "/health", timeout=10)
        assert health.json() == {"status": "ok", "workflow": NAME, "version": 4}
        assert httpx2.get(row["endpoint"] + "/schema", timeout=10).status_code == 401

        answer = {"step": "approve", "decision": "approved"}
        core_id = env.call("operator", "POST", f"/workflows/{NAME}/runs", json=QUARTER).json()[
            "run_id"
        ]
        env.call("operator", "POST", f"/runs/{core_id}/decisions", json=answer)
        started = httpx2.post(row["endpoint"] + "/runs", json=QUARTER, headers=key, timeout=30)
        assert started.status_code == 202
        service_id = started.json()["run_id"]
        service_report = _wait_for(row["endpoint"], key, service_id, "WAITING")
        assert service_report["status"] == "WAITING"
        decided = httpx2.post(
            f"{row['endpoint']}/runs/{service_id}/decisions", json=answer, headers=key, timeout=30
        )
        assert decided.status_code == 202
        service_report = _wait_for(row["endpoint"], key, service_id, "FINISHED")
        core_report = env.call("operator", "GET", f"/runs/{core_id}").json()
        assert normalised(service_report) == normalised(core_report)
        assert core_report["outcome"] == "SUCCEEDED"
    finally:
        env.call("admin", "DELETE", f"/deployments/{row['id']}")
    with pytest.raises(httpx2.TransportError):  # the process is gone: nobody answers
        httpx2.get(row["endpoint"] + "/health", timeout=2)


def _wait_for(endpoint: str, key: dict[str, str], run_id: str, status: str) -> dict[str, Any]:
    """Poll a run of a service until it has the status (the real process drives it on a thread)."""
    import time

    deadline = time.monotonic() + 30
    report: dict[str, Any] = {}
    while time.monotonic() < deadline:
        report = httpx2.get(f"{endpoint}/runs/{run_id}", headers=key, timeout=10).json()
        if report["status"] == status:
            return report
        time.sleep(0.1)
    raise AssertionError(f"the run did not reach {status}: {report}")


# -- a container, where there is a daemon -----------------------------------------------------
@pytest.mark.docker
def test_a_service_started_as_a_container_answers(tmp_path: Path) -> None:
    docker = pytest.importorskip("docker")
    try:
        docker.from_env().ping()
    except Exception as error:
        pytest.skip(f"no Docker daemon here: {error}")
    from flowstat.deploy.launchers import DockerLauncher

    env = make_env(tmp_path, packager=build_bundle, launchers={"docker": DockerLauncher()})
    publish_four(env)
    made = env.call(
        "admin", "POST", "/deployments", json={"workflow": NAME, "version": 4, "kind": "docker"}
    )
    assert made.status_code == 201, made.text
    row = made.json()
    try:
        health = httpx2.get(row["endpoint"] + "/health", timeout=10)
        assert health.json() == {"status": "ok", "workflow": NAME, "version": 4}
    finally:
        env.call("admin", "DELETE", f"/deployments/{row['id']}")
