"""A thread-pool driver for the scheduler.

The scheduler is a single-threaded state machine; this driver is the only place where threads
exist. Worker threads only run the action of a step and put the outcome in a queue; the thread
that called ``run_threaded`` feeds the outcomes to the scheduler one by one. So the scheduler needs
no lock, and a run decided here is decided exactly as in the simulated driver.

Time comes from the ``clock`` argument and waiting from the ``sleep`` argument (deterministic mode):
the driver never reads a clock and never sleeps by itself. The delay before a retry is slept in the
worker thread, so the step keeps its slot and the scheduler thread stays free.
"""

from __future__ import annotations

import queue
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from flowstat.engine.scheduler import Cancel, Command, Scheduler, Start


@dataclass(frozen=True, slots=True)
class Ok:
    output: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Error:
    code: str
    message: str
    transient: bool = False  # a retry may help; set by the action, never guessed here


@dataclass(frozen=True, slots=True)
class Pause:
    """The step needs an answer from a person before it can finish."""


Outcome = Ok | Error | Pause
Executor = Callable[[Start], Outcome]
Answerer = Callable[[Start], Mapping[str, object] | None]


def run_threaded(
    scheduler: Scheduler,
    execute: Executor,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    answer: Answerer | None = None,
) -> None:
    """Run the scheduler to the end, or until only waiting steps are left.

    ``answer`` is asked when a step pauses; an answer finishes the step, ``None`` leaves it
    WAITING (the run then returns with no outcome). A step cancelled while its thread is still
    running is not interrupted; its late result is ignored.
    """
    results: queue.Queue[tuple[Start, Outcome]] = queue.Queue()
    cancelled: set[str] = set()
    in_flight = 0

    def work(command: Start) -> None:
        if command.delay > 0:
            sleep(command.delay)
        try:
            outcome = execute(command)
        except Exception as exc:  # an action may fail in any way; the step fails, the run goes on
            outcome = Error("E-ACT-999", f"{type(exc).__name__}: {exc}")
        results.put((command, outcome))

    with ThreadPoolExecutor(max_workers=scheduler.max_parallel) as pool:

        def dispatch(commands: list[Command]) -> None:
            nonlocal in_flight
            for command in commands:
                if isinstance(command, Cancel):
                    cancelled.add(command.step)
                else:
                    in_flight += 1
                    pool.submit(work, command)

        dispatch(scheduler.start(clock()))
        while in_flight:
            command, outcome = results.get()
            in_flight -= 1
            if command.step in cancelled:
                continue
            step = command.step
            if isinstance(outcome, Ok):
                commands = scheduler.step_done(step, dict(outcome.output), clock())
            elif isinstance(outcome, Error):
                commands = scheduler.step_failed(
                    step, outcome.code, outcome.message, clock(), outcome.transient
                )
            else:
                commands = scheduler.step_waiting(step, clock())
                reply = answer(command) if answer is not None else None
                if reply is not None:
                    commands += scheduler.signal(step, dict(reply), clock())
            dispatch(commands)
