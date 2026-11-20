"""Drive a stored run with the real actions on a thread pool (command line and API share it).

What performs the steps (the files, the target database, the clock, the sleep) is a ``Machine``
that the caller builds, so this module reads neither the clock nor the environment.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from flowstat.actions.base import ActionContext
from flowstat.actions.registry import default_registry
from flowstat.engine.runner import Executor, run_threaded
from flowstat.engine.scheduler import Command, Start
from flowstat.model.history import RunReport
from flowstat.service.runs import Launch, RunService


@dataclass(frozen=True, slots=True)
class Machine:
    data_dir: Path  # the regional forms
    target: str  # the database the results are written to
    workers: int  # steps of one run that may run at the same time
    clock: Callable[[], float]
    sleep: Callable[[float], None]
    hosts: frozenset[str] | None = None  # the hosts http.call may reach (US-39); None: any


def drive(
    runs: RunService,
    run_id: str,
    launch: Launch,
    machine: Machine,
    commands: list[Command] | None = None,
    answer: Callable[[Start], Mapping[str, object] | None] | None = None,
    wrap: Callable[[Executor], Executor] | None = None,
) -> RunReport:
    """Run the scheduler to the end or to a waiting step; its events are stored before its
    commands run (write-ahead). ``commands`` continue a restore or an answer. ``wrap`` puts
    something in front of the actions (the kept results of a run of a campaign, US-58)."""
    base = ActionContext(run_id, "", machine.data_dir, machine.target)

    def journal() -> None:
        launch.log.commit(launch.scheduler.events)

    execute = default_registry(machine.hosts).executor(base)
    run_threaded(
        launch.scheduler,
        execute if wrap is None else wrap(execute),
        machine.clock,
        machine.sleep,
        answer,
        journal=journal,
        commands=commands,
    )
    return runs.report(run_id)
