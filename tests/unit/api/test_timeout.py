"""The time limit of an approval, as the core sees it (US-38, docs/api-spec.md section 5)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from flowstat.actions.registry import default_registry
from flowstat.api.app import create_app
from flowstat.api.expiring import expire_due
from flowstat.api.locks import RunLocks
from flowstat.service.expiring import Limits
from tests.unit.actions.helpers import write_all_forms
from tests.unit.api.helpers import EXAMPLE, NAME, Env, make_env

LIMITED = EXAMPLE.replace(
    "    with: {role: chief_statistician}\n",
    "    with: {role: chief_statistician}\n    timeout: 3600\n",
    1,
)
RUN = "r-0001"


class Clock:
    """The time of the events of a run: it moves only when a test moves it."""

    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def environment(tmp_path: Path, text: str = LIMITED) -> tuple[Env, Clock]:
    clock = Clock()
    base = make_env(tmp_path)
    config = replace(base.config, time=clock)
    env = Env(TestClient(create_app(config)), config)
    assert env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": text})
    assert env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish").status_code == 200
    write_all_forms(config.data_dir, "2025Q1")
    return env, clock


def start(env: Env) -> None:
    response = env.call(
        "operator", "POST", f"/workflows/{NAME}/runs", json={"inputs": {"quarter": "2025Q1"}}
    )
    assert response.status_code == 202, response.text


def report(env: Env) -> dict[str, Any]:
    body: dict[str, Any] = env.call("operator", "GET", f"/runs/{RUN}").json()
    return body


def approve(env: Env) -> Any:
    return env.call(
        "operator",
        "POST",
        f"/runs/{RUN}/decisions",
        json={"step": "approve", "decision": "approved"},
    )


def sweep(env: Env, limits: Limits | None = None) -> list[str]:
    return expire_due(env.config, default_registry(), RunLocks(), limits)


def test_an_approval_nobody_answers_fails_when_its_time_runs_out(tmp_path: Path) -> None:
    env, clock = environment(tmp_path)
    start(env)
    assert report(env)["status"] == "WAITING"
    clock.now = 100.0 + 3599.0
    assert sweep(env) == [] and report(env)["status"] == "WAITING"
    clock.now = 100.0 + 3600.0
    assert sweep(env) == [RUN]
    done = report(env)
    approve_step = next(s for s in done["steps"] if s["step"] == "approve")
    assert approve_step["state"] == "FAILED" and approve_step["code"] == "E-RUN-009"
    assert done["status"] == "FINISHED" and done["outcome"] == "FAILED"


def test_the_limit_is_measured_from_the_wait_and_not_from_the_start_of_the_run(
    tmp_path: Path,
) -> None:
    env, clock = environment(tmp_path)
    start(env)
    waited_from = next(s for s in report(env)["steps"] if s["step"] == "approve")
    assert waited_from["state"] == "WAITING"
    clock.now = 100.0 + 3600.0 - 1
    assert sweep(env) == []


def test_an_answer_in_time_ends_the_wait_and_nothing_expires_afterwards(tmp_path: Path) -> None:
    env, clock = environment(tmp_path)
    start(env)
    clock.now = 200.0
    assert approve(env).status_code == 202
    assert report(env)["outcome"] == "SUCCEEDED"
    clock.now = 1e9
    assert sweep(env) == []


def test_an_answer_that_comes_after_the_limit_is_refused_and_the_step_fails_at_the_next_check(
    tmp_path: Path,
) -> None:
    env, clock = environment(tmp_path)
    start(env)
    clock.now = 100.0 + 3600.0
    late = approve(env)
    assert late.status_code == 409 and late.json()["errors"][0]["code"] == "E-API-003"
    assert "too late" in late.json()["errors"][0]["message"]
    assert report(env)["status"] == "WAITING"  # the refusal stores nothing
    assert sweep(env) == [RUN]
    assert report(env)["outcome"] == "FAILED"


def test_a_core_that_starts_after_the_limit_fails_the_step_at_once(tmp_path: Path) -> None:
    env, clock = environment(tmp_path)
    start(env)
    clock.now = 100.0 + 86400.0  # the process was down for a day
    restarted = Env(TestClient(create_app(env.config)), env.config)
    done = report(restarted)
    assert done["outcome"] == "FAILED"
    assert next(s for s in done["steps"] if s["step"] == "approve")["code"] == "E-RUN-009"


def test_a_workflow_without_a_limit_waits_for_ever(tmp_path: Path) -> None:
    env, clock = environment(tmp_path, EXAMPLE)
    start(env)
    clock.now = 1e12
    assert sweep(env) == [] and report(env)["status"] == "WAITING"
    restarted = Env(TestClient(create_app(env.config)), env.config)
    assert report(restarted)["status"] == "WAITING"


def test_the_limit_of_a_version_is_looked_up_once(tmp_path: Path) -> None:
    env, _ = environment(tmp_path)
    start(env)
    limits = Limits()
    sweep(env, limits)
    sweep(env, limits)
    assert limits._known == {(NAME, 1): True}


def test_a_run_that_is_being_driven_is_left_for_the_next_check(tmp_path: Path) -> None:
    env, clock = environment(tmp_path)
    start(env)
    clock.now = 1e9
    locks = RunLocks()
    assert locks.try_acquire(RUN)
    assert expire_due(env.config, default_registry(), locks) == []
    assert report(env)["status"] == "WAITING"


def test_a_time_limit_on_a_step_that_does_not_wait_cannot_be_published(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    bad = EXAMPLE.replace("  consolidate:\n", "  consolidate:\n    timeout: 60\n", 1)
    response = env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": bad})
    assert response.status_code == 201  # a draft is saved with its diagnostics
    assert [d["code"] for d in response.json()["diagnostics"]] == ["E-TYPE-006"]
    published = env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish")
    assert published.status_code == 422


# -- the thread that looks every few seconds ------------------------------------------------------
def test_the_ticker_calls_its_work_until_it_is_stopped_and_survives_an_error() -> None:
    import threading

    from flowstat.api.edge import Ticker

    calls: list[int] = []
    enough = threading.Event()

    def work() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("one bad round")
        if len(calls) >= 3:
            enough.set()

    ticker = Ticker(work, 0.01)
    ticker.start()
    assert enough.wait(5.0)
    assert ticker.stop(5.0)
    seen = len(calls)
    threading.Event().wait(0.05)
    assert len(calls) == seen  # stopped


def test_the_expiry_thread_is_off_when_the_interval_is_not_positive(tmp_path: Path) -> None:
    from flowstat.api.expiring import start_expiry

    env, _ = environment(tmp_path)
    assert start_expiry(env.client.app, env.config, 0) is None  # type: ignore[arg-type]


def test_the_expiry_thread_fails_the_step_by_itself(tmp_path: Path) -> None:
    import threading
    import time

    from flowstat.api.expiring import start_expiry

    env, clock = environment(tmp_path)
    start(env)
    clock.now = 1e9
    ticker = start_expiry(env.client.app, env.config, 0.01)  # type: ignore[arg-type]
    assert ticker is not None
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and report(env)["status"] != "FINISHED":
            threading.Event().wait(0.02)
    finally:
        assert ticker.stop(5.0)
    assert report(env)["outcome"] == "FAILED"
