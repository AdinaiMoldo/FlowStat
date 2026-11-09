"""US-31: As an admin, I see the runs of deployed services in the core.

Given a deployed service completes a run, when its outbox ships the events, then the core shows
the history; the same batch delivered twice gives no duplicates; the registry shows the version,
the endpoint and the status. The service is a real process and the core a real server, so the key,
the environment, the thread and the HTTP between them are the ones that run in production.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx2
import pytest
import uvicorn

from flowstat.deploy.launchers import ProcessLauncher
from flowstat.deploy.package import build_bundle
from flowstat.model.events import event_to_dict
from flowstat.repo.runs import RunStore
from tests.acceptance.test_us30 import QUARTER, ROOT, _wait_for, publish_four
from tests.unit.actions.helpers import write_all_forms
from tests.unit.api.helpers import NAME, Env, make_env

ANSWER = {"step": "approve", "decision": "approved"}


def popen(command: list[str], **options: Any) -> subprocess.Popen[bytes]:
    """Start the service with the source tree on its path (the package may not be installed)."""
    environment = dict(options.pop("env", os.environ))
    environment["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.Popen(command, env=environment, **options)


@contextmanager
def core_listening(env: Env, port: int) -> Iterator[None]:
    """The core of ``env`` answering on ``port`` (a real server on a thread)."""
    server = uvicorn.Server(
        uvicorn.Config(env.client.app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    until(lambda: server.started, "the core did not start")
    try:
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def until(condition: Callable[[], Any], what: str, limit: float = 60.0) -> Any:
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        found = condition()
        if found:
            return found
        time.sleep(0.1)
    raise AssertionError(what)


def free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def make_core(tmp_path: Path, port: int) -> Env:
    env = make_env(
        tmp_path,
        packager=build_bundle,
        launchers={"process": ProcessLauncher(popen=popen, limit=60.0)},
    )
    env.client.app.state.config = replace(  # type: ignore[attr-defined]
        env.config, ingest_url=f"http://127.0.0.1:{port}"
    )
    publish_four(env)
    write_all_forms(env.config.data_dir, "2025Q1")
    return env


def deploy(env: Env) -> dict[str, Any]:
    made = env.call("admin", "POST", "/deployments", json={"workflow": NAME, "version": 4})
    assert made.status_code == 201, made.text
    row: dict[str, Any] = made.json()
    write_all_forms(env.config.bundles / row["id"] / "data", "2025Q1")
    return row


def run_to_the_end(row: dict[str, Any]) -> str:
    """One run of the service through its approval; returns its id."""
    key = {"Authorization": f"Bearer {row['api_key']}"}
    started = httpx2.post(row["endpoint"] + "/runs", json=QUARTER, headers=key, timeout=30)
    assert started.status_code == 202
    run_id: str = started.json()["run_id"]
    _wait_for(row["endpoint"], key, run_id, "WAITING")
    title = {"Authorization": f"Bearer {row['title_keys']['chief_statistician']}"}
    decided = httpx2.post(
        f"{row['endpoint']}/runs/{run_id}/decisions", json=ANSWER, headers=title, timeout=30
    )
    assert decided.status_code == 202
    _wait_for(row["endpoint"], key, run_id, "FINISHED")
    return run_id


def service_events(env: Env, row: dict[str, Any], run_id: str) -> list[Any]:
    db = sqlite3.connect(env.config.bundles / row["id"] / "service.db")
    try:
        return RunStore(db).events(run_id)
    finally:
        db.close()


def core_shows(env: Env, deployment: str, run_id: str, status: str) -> bool:
    runs = env.call("operator", "GET", f"/runs?deployment={deployment}").json()
    return any(r["run_id"] == run_id and r["status"] == status for r in runs)


def stop(env: Env, row: dict[str, Any]) -> None:
    env.call("admin", "DELETE", f"/deployments/{row['id']}")


def test_the_core_shows_the_history_of_a_run_of_a_deployed_service(tmp_path: Path) -> None:
    port = free_port()
    env = make_core(tmp_path, port)
    with core_listening(env, port):
        row = deploy(env)
        try:
            run_id = run_to_the_end(row)
            until(lambda: core_shows(env, row["id"], run_id, "FINISHED"), "the run did not arrive")

            # the core reads it like its own: the same report as the service gives
            key = {"Authorization": f"Bearer {row['api_key']}"}
            in_service = httpx2.get(f"{row['endpoint']}/runs/{run_id}", headers=key).json()
            in_core = env.call("operator", "GET", f"/runs/{run_id}").json()
            assert in_core == in_service
            assert in_core["outcome"] == "SUCCEEDED"

            # the history is the whole of it, event for event
            until(
                lambda: (
                    len(service_events(env, row, run_id))
                    == len(RunStore(sqlite3.connect(env.config.db_path)).events(run_id))
                ),
                "the core holds fewer events than the service",
            )

            # the registry says what runs where
            [listed] = env.call("operator", "GET", "/deployments").json()
            assert (listed["version"], listed["status"], listed["endpoint"]) == (
                4,
                "running",
                row["endpoint"],
            )
            assert listed["runs"] == 1 and listed["last_event_at"] is not None
            [summary] = env.call("operator", "GET", f"/runs?deployment={row['id']}").json()
            assert summary["origin"] == row["id"] and summary["version"] == 4
        finally:
            stop(env, row)


def test_the_same_batch_delivered_twice_leaves_no_duplicates(tmp_path: Path) -> None:
    port = free_port()
    env = make_core(tmp_path, port)
    with core_listening(env, port):
        row = deploy(env)
        try:
            run_id = run_to_the_end(row)
            until(lambda: core_shows(env, row["id"], run_id, "FINISHED"), "the run did not arrive")
            events = service_events(env, row, run_id)
            until(lambda: len(core_events(env, run_id)) == len(events), "history incomplete")

            # the service's own thread has shipped it; now deliver the whole history again, by hand
            stored = env.call("operator", "GET", f"/runs/{run_id}").json()
            batch = {
                "run": {
                    "run_id": run_id,
                    "workflow": NAME,
                    "version": 4,
                    "inputs": QUARTER["inputs"],
                    "created_at": _created_at(env, run_id),
                },
                "events": [event_to_dict(e) for e in events[:200]],
            }
            key = {"Authorization": f"Bearer {row['ingest_key']}"}
            url = f"{env.client.base_url}/deployments/{row['id']}/events"
            first = env.client.post(url, json=batch, headers=key)
            second = env.client.post(url, json=batch, headers=key)
            assert first.status_code == second.status_code == 200
            assert len(core_events(env, run_id)) == len(events)
            assert env.call("operator", "GET", f"/runs/{run_id}").json() == stored
            assert len(env.call("operator", "GET", "/runs").json()) == 1
        finally:
            stop(env, row)


def core_events(env: Env, run_id: str) -> list[Any]:
    db = sqlite3.connect(env.config.db_path)
    try:
        return RunStore(db).events(run_id)
    finally:
        db.close()


def _created_at(env: Env, run_id: str) -> str:
    db = sqlite3.connect(env.config.db_path)
    try:
        return RunStore(db).get(run_id).created_at
    finally:
        db.close()


def test_a_core_that_is_away_gets_the_history_when_it_comes_back(tmp_path: Path) -> None:
    port = free_port()
    env = make_core(tmp_path, port)
    row = deploy(env)  # nobody listens at the address the service was given
    try:
        run_id = run_to_the_end(row)
        log = env.config.bundles / row["id"] / "service.log"
        until(lambda: "events are not shipped" in log.read_text(errors="replace"), "no retry")
        assert env.call("operator", "GET", "/runs").json() == []  # the core has nothing yet
        with core_listening(env, port):
            until(lambda: core_shows(env, row["id"], run_id, "FINISHED"), "never delivered", 90.0)
            assert len(core_events(env, run_id)) == len(service_events(env, row, run_id))
    finally:
        stop(env, row)


def test_a_deployment_without_the_address_of_the_core_ships_nothing(tmp_path: Path) -> None:
    env = make_env(
        tmp_path,
        packager=build_bundle,
        launchers={"process": ProcessLauncher(popen=popen, limit=60.0)},
    )  # ingest_url stays None
    publish_four(env)
    write_all_forms(env.config.data_dir, "2025Q1")
    row = deploy(env)
    try:
        run_id = run_to_the_end(row)
        time.sleep(2.5)
        assert env.call("operator", "GET", "/runs").json() == []
        assert len(service_events(env, row, run_id)) > 20  # the log of the service is complete
        assert env.call("operator", "GET", f"/deployments/{row['id']}").json()["runs"] == 0
    finally:
        stop(env, row)


@pytest.mark.parametrize("who", ["no key", "a user of the core"])
def test_the_core_refuses_events_from_anyone_but_the_service(tmp_path: Path, who: str) -> None:
    env = make_env(tmp_path)
    headers = {} if who == "no key" else {"Authorization": "Bearer " + "a1" * 32}
    response = env.client.post("/deployments/x/events", json={}, headers=headers)
    assert response.status_code == (401 if who == "no key" else 403)
