"""Acceptance test of US-13: a transient error is retried with growing delays."""

from __future__ import annotations

import itertools

from flowstat.engine.runner import Error, Ok, Outcome, run_threaded
from flowstat.engine.scheduler import Scheduler, Start
from flowstat.engine.simulation import Done, Fail, Result, simulate
from flowstat.model.events import EventKind
from flowstat.model.states import RunOutcome, StepState
from tests.unit.engine.helpers import doc, plan_of

STEPS = "  fetch:\n    action: x.y\n    retry: {max: 3}\n"
PLAN = plan_of(doc(STEPS, "goals: {any_of: [fetch]}\n"))


def test_us13_an_action_failing_twice_succeeds_on_the_third_attempt_with_growing_delays() -> None:
    # Given retry: {max: 3} and an action failing twice
    plan = plan_of(
        doc(
            STEPS.replace("{max: 3}", "{max: 3, backoff: exponential}"),
            "goals: {any_of: [fetch]}\n",
        )
    )
    delays: list[float] = []

    def execute(command: Start) -> Outcome:
        if command.attempt <= 2:
            return Error("E-ACT-008", "connection lost", transient=True)
        return Ok({"rows": 9})

    # When I run
    scheduler = Scheduler(plan, {}, 1)
    run_threaded(scheduler, execute, lambda: 0.0, delays.append)

    # Then the step succeeds on the third attempt, with growing delays
    assert scheduler.states["fetch"] is StepState.SUCCEEDED
    assert scheduler.outputs["fetch"] == {"rows": 9}
    assert scheduler.outcome is RunOutcome.SUCCEEDED
    assert delays == [1.0, 2.0]
    assert delays[0] < delays[1]
    attempts = [e.attempt for e in scheduler.events if e.kind is EventKind.STEP_RETRY]
    assert attempts == [2, 3]


def test_us13_the_same_story_on_the_virtual_clock_shows_the_times() -> None:
    def act(command: Start) -> Result:
        if command.attempt <= 2:
            return Fail(1.0, "E-ACT-008", "connection lost", transient=True)
        return Done(1.0, {})

    plan = plan_of(
        doc(
            STEPS.replace("{max: 3}", "{max: 3, backoff: exponential}"),
            "goals: {any_of: [fetch]}\n",
        )
    )
    sim = simulate(plan, {}, 1, act)
    starts = sim.attempts["fetch"]
    assert [round(b - a, 6) for a, b in itertools.pairwise(starts)] == [2.0, 3.0]
    # the gap between two attempts is the running time (1) plus the delay (1, then 2)
