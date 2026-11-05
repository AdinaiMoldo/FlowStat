"""A simulated driver for the scheduler: virtual time, a seeded order of simultaneous events.

It executes the commands of the scheduler against a ``behavior`` (what each step does and how long
it takes) without threads and without sleeping. Tests use it for exact checks of the four join
rules and for property tests; the benchmark of the priority rules uses it for makespans. The seed
only decides the order of events that happen at the same virtual time.
"""

from __future__ import annotations

import heapq
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from flowstat.compiler.plan import Plan
from flowstat.engine.scheduler import Cancel, Command, Scheduler, Start
from flowstat.model.states import TERMINAL_STEP_STATES


@dataclass(frozen=True, slots=True)
class Done:
    duration: float = 1.0
    output: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class Fail:
    duration: float = 1.0
    code: str = "E-ACT-001"
    message: str = "failed"
    transient: bool = False  # a retry may help (docs/dsl-spec.md 5.3)


@dataclass(frozen=True, slots=True)
class Wait:
    duration: float = 0.0  # time until the step reports that it is waiting


Result = Done | Fail | Wait
Behavior = Callable[[Start], Result]


@dataclass
class Simulation:
    scheduler: Scheduler
    makespan: float  # virtual time from the start to the last event
    started: dict[str, float]  # when the first attempt of each step started
    finished: dict[str, float]
    attempts: dict[str, list[float]]  # start time of every attempt (after its delay)


def simulate(
    plan: Plan,
    inputs: Mapping[str, object],
    max_parallel: int,
    behavior: Behavior | None = None,
    durations: Mapping[str, float] | None = None,
    seed: int = 0,
    answers: Mapping[str, tuple[float, Mapping[str, object]]] | None = None,
    priority_durations: Mapping[str, float] | None = None,
    policy: str = "longest_path",
) -> Simulation:
    """Run the plan on a virtual clock.

    ``durations`` give the running time of every step (default 1.0) when no ``behavior`` is
    given. ``answers`` maps a waiting step to (delay after it started waiting, answer).
    ``priority_durations`` are the weights the scheduler uses for priorities (default: the same).
    ``policy`` is ``longest_path`` or ``fifo`` (the baseline of the benchmark).
    """
    weights = priority_durations if priority_durations is not None else durations
    scheduler = Scheduler(plan, inputs, max_parallel, durations=weights, policy=policy)
    rng = random.Random(seed)
    run_time = durations or {}

    def default_behavior(command: Start) -> Result:
        return Done(run_time.get(command.step, 1.0), {})

    act = behavior or default_behavior
    queue: list[tuple[float, float, int, str, str]] = []  # (time, tie-break, serial, kind, step)
    pending: dict[str, Result] = {}
    cancelled: set[str] = set()
    serial = 0
    started: dict[str, float] = {}
    finished: dict[str, float] = {}
    attempts: dict[str, list[float]] = {}

    def push(at: float, kind: str, step: str) -> None:
        nonlocal serial
        serial += 1
        heapq.heappush(queue, (at, rng.random(), serial, kind, step))

    def schedule(commands: list[Command], now: float) -> None:
        for command in commands:
            if isinstance(command, Cancel):
                cancelled.add(command.step)
                continue
            begin = now + command.delay  # a retry waits for its backoff first
            started.setdefault(command.step, begin)
            attempts.setdefault(command.step, []).append(begin)
            result = act(command)
            pending[command.step] = result
            kind = "wait" if isinstance(result, Wait) else "end"
            push(begin + result.duration, kind, command.step)

    now = 0.0
    schedule(scheduler.start(now), now)
    while queue:
        now, _, _, kind, step = heapq.heappop(queue)
        if step in cancelled:
            continue
        result = pending[step]
        if kind == "wait":
            commands = scheduler.step_waiting(step, now)
            if answers is not None and step in answers:
                delay, _ = answers[step]
                push(now + delay, "answer", step)
        elif kind == "answer":
            assert answers is not None
            commands = scheduler.signal(step, dict(answers[step][1]), now)
            finished[step] = now
        elif isinstance(result, Fail):
            commands = scheduler.step_failed(
                step, result.code, result.message, now, transient=result.transient
            )
            if scheduler.states[step] in TERMINAL_STEP_STATES:
                finished[step] = now
        else:
            assert isinstance(result, Done)
            commands = scheduler.step_done(step, dict(result.output or {}), now)
            finished[step] = now
        schedule(commands, now)
    return Simulation(scheduler, now, started, finished, attempts)
