"""A receiver that misbehaves (US-17): with the key of the operation the effect exists once.

The step calls ``http.call`` with a retry policy against the fault-injecting server. Every script
of faults is a way in which the answer, or the request, is lost; the run must end with the effect
applied exactly once. The last tests show where the guarantee stops: a receiver that ignores the
key, and a permanent error.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from flowstat.actions.base import ActionContext
from flowstat.actions.registry import default_registry
from flowstat.engine.runner import run_threaded
from flowstat.engine.scheduler import Scheduler
from flowstat.model.events import EventKind
from flowstat.model.states import RunOutcome, StepState
from tests.support.fault_server import FaultServer
from tests.unit.engine.helpers import doc, plan_of

RETRY = "{max: 3, backoff: fixed, delay: 0.01}"


def run_pay(server: FaultServer, timeout: float | None = None, retry: str = RETRY) -> Scheduler:
    extra = "" if timeout is None else f", timeout: {timeout}"
    steps = (
        "  pay:\n    action: http.call\n"
        f'    with: {{url: "${{inputs.url}}", body: {{amount: 5}}{extra}}}\n'
        f"    retry: {retry}\n"
    )
    plan = plan_of(doc(steps, "goals: {any_of: [pay]}\n", inputs="  url: {type: string}\n"))
    scheduler = Scheduler(plan, {"url": server.url}, 1)
    ticks = iter(range(10_000))
    base = ActionContext("run-1", "", Path("."), None)
    run_threaded(
        scheduler,
        default_registry().executor(base),
        lambda: float(next(ticks)),
        lambda seconds: None,
    )
    return scheduler


def attempts(scheduler: Scheduler) -> int:
    return 1 + sum(1 for e in scheduler.events if e.kind is EventKind.STEP_RETRY)


@pytest.mark.parametrize(
    "script",
    [
        ["drop_response", "ok"],
        ["drop_request", "ok"],
        ["fail_500", "ok"],
        ["fail_500_after_effect", "ok"],
        ["drop_response", "drop_response", "drop_response", "ok"],
        ["drop_response", "fail_500_after_effect", "drop_request", "ok"],
    ],
    ids=lambda s: "+".join(s),
)
def test_the_effect_exists_exactly_once(script: list[str]) -> None:
    # Given the server executes a request and drops the response (or fails in another way)
    with FaultServer(script) as server:
        # When the engine retries with the same key
        scheduler = run_pay(server)

    # Then the effect exists exactly once and the run succeeded
    assert scheduler.outcome is RunOutcome.SUCCEEDED
    assert len(server.effects) == 1
    assert attempts(scheduler) == len(script)
    assert len({r.key for r in server.requests}) == 1  # one operation, one key


def test_a_timeout_whose_request_still_takes_effect_later_gives_one_effect() -> None:
    with FaultServer(["slow", "ok"], slow_seconds=0.4) as server:
        scheduler = run_pay(server, timeout=0.1)
        time.sleep(0.6)  # the slow request finishes after the engine has moved on

    assert scheduler.outcome is RunOutcome.SUCCEEDED
    assert len(server.effects) == 1


def test_a_replayed_answer_is_the_answer_of_the_first_effect() -> None:
    with FaultServer(["drop_response", "ok"]) as server:
        scheduler = run_pay(server)

    body = scheduler.outputs["pay"]["body"]
    assert isinstance(body, dict) and body["charged"] == 1


def test_a_receiver_that_ignores_the_key_gets_the_effect_twice() -> None:
    # the stated limit (docs/dsl-spec.md 5.5): the engine can send the key, not make it honoured
    with FaultServer(["drop_response", "ok"], honour_keys=False) as server:
        scheduler = run_pay(server)

    assert scheduler.outcome is RunOutcome.SUCCEEDED
    assert len(server.effects) == 2


def test_a_permanent_error_is_not_retried() -> None:
    with FaultServer(["bad_request"]) as server:
        scheduler = run_pay(server)

    assert scheduler.states["pay"] is StepState.FAILED
    assert scheduler.errors["pay"][0] == "E-ACT-010"
    assert attempts(scheduler) == 1 and server.effects == []


def test_when_the_retries_run_out_the_step_fails_with_the_last_error() -> None:
    with FaultServer(["fail_500"] * 5) as server:
        scheduler = run_pay(server)

    assert scheduler.states["pay"] is StepState.FAILED
    assert scheduler.errors["pay"][0] == "E-ACT-008"
    assert attempts(scheduler) == 4 and len(server.requests) == 4 and server.effects == []


def test_a_lost_answer_without_a_retry_policy_fails_the_step_but_the_effect_exists() -> None:
    # without `retry` nothing repeats the call; the failure is honest, the effect is not hidden
    with FaultServer(["drop_response"]) as server:
        steps = (
            '  pay:\n    action: http.call\n    with: {url: "${inputs.url}", body: {amount: 5}}\n'
        )
        plan = plan_of(doc(steps, "goals: {any_of: [pay]}\n", inputs="  url: {type: string}\n"))
        scheduler = Scheduler(plan, {"url": server.url}, 1)
        base = ActionContext("run-1", "", Path("."), None)
        run_threaded(scheduler, default_registry().executor(base), lambda: 0.0, lambda s: None)

    assert scheduler.states["pay"] is StepState.FAILED
    assert len(server.effects) == 1
