"""Acceptance test of US-17: recovery against an action server that misbehaves."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from flowstat.actions.base import ActionContext
from flowstat.actions.registry import default_registry
from flowstat.engine.runner import run_threaded
from flowstat.model.events import EventKind
from flowstat.service.cli import main, open_services
from tests.support.fault_server import FaultServer

WORKFLOW = """workflow: payer
version: 1
inputs:
  url: {type: string}
steps:
  pay:
    action: http.call
    with: {url: "${inputs.url}", body: {amount: 5}}
    retry: {max: 3, backoff: fixed, delay: 0.05}
  confirm:
    action: http.call
    needs: [pay]
    with: {url: "${inputs.url}", body: {confirm: true}}
    retry: {max: 3, backoff: fixed, delay: 0.05}
goals: {any_of: [confirm]}
"""


class Killed(Exception):
    """The engine process died."""


def setup(tmp_path: Path) -> list[str]:
    workflow = tmp_path / "payer.yaml"
    workflow.write_text(WORKFLOW, encoding="utf8")
    db = str(tmp_path / "flowstat.db")
    assert main(["publish", str(workflow), "--db", db]) == 0
    (tmp_path / "forms").mkdir()
    return ["--db", db, "--data", str(tmp_path / "forms"), "--target", str(tmp_path / "t.db")]


def test_us17_a_dropped_response_is_retried_with_the_same_key_and_the_effect_exists_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given the server executes a request and drops the response
    where = setup(tmp_path)
    with FaultServer(["drop_response", "ok", "ok"]) as server:
        capsys.readouterr()

        # When the engine retries with the same key
        code = main(["run", "payer", *where, "--input", f"url={server.url}", "--run-id", "p1"])

    # Then the effect exists exactly once
    out = capsys.readouterr().out
    assert code == 0, out
    pay_effects = [e for e in server.effects if e.body == {"amount": 5}]
    assert len(pay_effects) == 1
    pay = next(line.split() for line in out.splitlines() if line.startswith("pay"))
    assert pay[1] == "SUCCEEDED" and pay[-1] == "2"  # a second attempt
    assert {r.key for r in server.requests if r.body == {"amount": 5}} == {"p1/pay"}


def test_us17_the_engine_dies_after_the_effect_and_the_repeat_after_the_restart_has_no_new_effect(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    where = setup(tmp_path)
    with FaultServer() as server:
        _, runs = open_services(where[1])
        runs.start("p2", "payer", 1, {"url": server.url}, "2026-11-05T10:00:00Z")
        launch = runs.launch("p2", 2)

        def journal() -> None:
            done = any(
                e.kind is EventKind.STEP_FINISHED and e.step == "pay"
                for e in launch.scheduler.events
            )
            if done:
                raise Killed  # the effect of pay exists, its end is not stored
            launch.log.commit(launch.scheduler.events)

        base = ActionContext("p2", "", tmp_path / "forms", None)
        with pytest.raises(Killed):
            run_threaded(
                launch.scheduler,
                default_registry().executor(base),
                time.time,
                time.sleep,
                journal=journal,
            )
        assert len(server.effects) == 1
        capsys.readouterr()

        code = main(["resume", "p2", *where])

    out = capsys.readouterr().out
    assert code == 0, out
    assert [e.body for e in server.effects] == [{"amount": 5}, {"confirm": True}]
    assert [r.key for r in server.requests] == ["p2/pay", "p2/pay", "p2/confirm"]
