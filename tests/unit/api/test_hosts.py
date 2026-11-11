"""The hosts http.call may reach, as the core enforces them (US-39, docs/dsl-spec.md 5.5)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from flowstat.api.app import create_app
from tests.support.fault_server import FaultServer
from tests.unit.api.helpers import Env, make_env

ALLOWED = frozenset({"api.example.kg"})


def workflow(url: str) -> str:
    return (
        "workflow: pay\nversion: 1\n"
        "inputs:\n  target: {type: string}\n"
        f"steps:\n  charge:\n    action: http.call\n    with: {{url: '{url}'}}\n"
        "goals: {all_of: [charge]}\n"
    )


def environment(tmp_path: Path, hosts: frozenset[str] | None = ALLOWED) -> Env:
    config = replace(make_env(tmp_path).config, allowed_hosts=hosts)
    return Env(TestClient(create_app(config)), config)


def save(env: Env, url: str) -> Any:
    return env.call("designer", "POST", "/workflows/pay/versions", json={"text": workflow(url)})


def test_a_constant_url_outside_the_list_is_refused_when_the_version_is_saved(
    tmp_path: Path,
) -> None:
    env = environment(tmp_path)
    saved = save(env, "https://other.example.kg/v1")
    assert "E-TYPE-007" in str(saved.json())  # said when the draft is saved
    published = env.call("designer", "POST", "/workflows/pay/versions/1/publish")
    assert published.status_code == 422 and "E-TYPE-007" in str(published.json())


def test_a_constant_url_on_the_list_can_be_published(tmp_path: Path) -> None:
    env = environment(tmp_path)
    save(env, "https://api.example.kg/v1")
    assert env.call("designer", "POST", "/workflows/pay/versions/1/publish").status_code == 200


def test_without_a_list_any_constant_url_can_be_published(tmp_path: Path) -> None:
    env = environment(tmp_path, hosts=None)
    save(env, "https://other.example.kg/v1")
    assert env.call("designer", "POST", "/workflows/pay/versions/1/publish").status_code == 200


def test_a_url_that_a_reference_builds_is_refused_by_the_run_before_anything_is_sent(
    tmp_path: Path,
) -> None:
    env = environment(tmp_path)
    save(env, "${inputs.target}")
    assert env.call("designer", "POST", "/workflows/pay/versions/1/publish").status_code == 200
    with FaultServer() as server:
        started = env.call(
            "operator", "POST", "/workflows/pay/runs", json={"inputs": {"target": server.url}}
        )
        assert started.status_code == 202
        report = env.call("operator", "GET", f"/runs/{started.json()['run_id']}").json()
        assert server.requests == []  # the host was not on the list: nothing was sent
    [charge] = report["steps"]
    assert charge["state"] == "FAILED" and charge["code"] == "E-ACT-011"
    assert report["outcome"] == "FAILED"


def test_the_same_run_with_a_host_on_the_list_goes_through(tmp_path: Path) -> None:
    with FaultServer() as server:
        env = environment(tmp_path, frozenset({"127.0.0.1"}))
        save(env, "${inputs.target}")
        env.call("designer", "POST", "/workflows/pay/versions/1/publish")
        started = env.call(
            "operator", "POST", "/workflows/pay/runs", json={"inputs": {"target": server.url}}
        )
        report = env.call("operator", "GET", f"/runs/{started.json()['run_id']}").json()
        assert len(server.requests) == 1
    assert report["outcome"] == "SUCCEEDED"
