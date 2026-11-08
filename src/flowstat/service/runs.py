"""Start a run of a published version, keep its history, report it (US-11).

Identifiers and times come from the caller (deterministic mode). The way steps are executed is a
``driver``: a function that takes the plan and the validated inputs and returns the events of the
run. The simulated driver of the tests and the thread-pool driver of the command line are two such
functions; this service does not care which.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass

from flowstat.analysis.critical import priorities
from flowstat.compiler.inputs import InputError
from flowstat.compiler.plan import Plan
from flowstat.engine.scheduler import Command, Scheduler
from flowstat.model.events import Event
from flowstat.model.history import RunReport, report_from
from flowstat.model.states import StepState
from flowstat.model.versions import VersionStatus
from flowstat.recovery.saga import compensation_blockers
from flowstat.recovery.wal import WriteAheadLog
from flowstat.repo.runs import RunStore, StoredRun
from flowstat.service.workflows import WorkflowService

Driver = Callable[[Plan, Mapping[str, object]], list[Event]]


class NotPublishedError(RuntimeError):
    """Only a published version can run: a draft may still change."""


class InvalidInputsError(ValueError):
    def __init__(self, errors: list[InputError]) -> None:
        super().__init__("; ".join(f"{e.code} {e.message}" for e in errors))
        self.errors = errors


class StepNotFoundError(LookupError):
    """The run has no step of that name."""


class StepNotWaitingError(RuntimeError):
    """An answer was sent to a step that is not waiting for one."""


class DecisionNotDeclaredError(ValueError):
    def __init__(self, decision: str, allowed: tuple[str, ...]) -> None:
        super().__init__(f"'{decision}' is not one of: {', '.join(allowed)}")
        self.allowed = allowed


class TitleMissingError(PermissionError):
    """The caller does not hold the title the approval asks for (DR-04)."""

    def __init__(self, title: str) -> None:
        super().__init__(f"the title '{title}' is needed to answer this step")
        self.title = title


@dataclass(frozen=True, slots=True)
class Launch:
    """A scheduler for a stored run and the log that keeps its history (write-ahead)."""

    scheduler: Scheduler
    log: WriteAheadLog
    commands: list[Command]  # what to dispatch first: empty for a new run (the driver starts it)


class RunService:
    def __init__(self, db: sqlite3.Connection, workflows: WorkflowService) -> None:
        self.store = RunStore(db)
        self.workflows = workflows

    def start(
        self, run_id: str, name: str, number: int, inputs: Mapping[str, object], now: str
    ) -> Plan:
        """Check the version and the inputs, then store the run. Returns the plan to execute."""
        version = self.workflows.get(name, number)
        if version.status is not VersionStatus.PUBLISHED:
            raise NotPublishedError(f"{name} v{number} is a draft; publish it before running")
        resolved, errors = self.workflows.validate_inputs(name, number, dict(inputs))
        if errors:
            raise InvalidInputsError(errors)
        self.store.create(run_id, name, number, resolved, now)
        return self.workflows.get_plan(name, number)

    def execute(
        self,
        run_id: str,
        name: str,
        number: int,
        inputs: Mapping[str, object],
        now: str,
        driver: Driver,
    ) -> RunReport:
        plan = self.start(run_id, name, number, inputs, now)
        self.store.append(run_id, driver(plan, self.store.get(run_id).inputs))
        return self.report(run_id)

    def launch(self, run_id: str, workers: int) -> Launch:
        """A scheduler for a run that has no history yet."""
        run = self.store.get(run_id)
        plan = self.workflows.get_plan(run.workflow, run.version)
        scheduler = self._scheduler(plan, run.inputs, workers)
        return Launch(scheduler, WriteAheadLog(self.store, run_id), [])

    def recover(
        self, run_id: str, workers: int, now: float, idempotent: Callable[[str], bool]
    ) -> Launch:
        """Rebuild a run from its stored history after a crash (docs/dsl-spec.md 5.5).

        Nothing is stored here: the events of the recovery (RUN_RESUMED, steps that became
        UNCERTAIN) are committed by the driver before the first command is dispatched.
        """
        run = self.store.get(run_id)
        plan = self.workflows.get_plan(run.workflow, run.version)
        events = self.store.events(run_id)
        scheduler = self._scheduler(plan, run.inputs, workers)
        commands = scheduler.restore(events, now, idempotent)
        return Launch(scheduler, WriteAheadLog(self.store, run_id, stored=len(events)), commands)

    def declared_decisions(self, action: str, args: Mapping[str, object]) -> tuple[str, ...] | None:
        """The decisions a waiting step accepts: its argument ``decisions`` or the default of its
        action. None when the action has no decision (it is not an approval)."""
        signature = self.workflows.signatures.get(action)
        choice = signature.choices.get("decision") if signature is not None else None
        if choice is None:
            return None
        given = args.get(choice.argument)
        if isinstance(given, list) and given and all(isinstance(g, str) for g in given):
            return tuple(given)
        return choice.default

    def answer(
        self,
        launch: Launch,
        step: str,
        decision: str,
        by: str,
        titles: Collection[str] | None,
        now: float,
    ) -> list[Command]:
        """Check an answer to a waiting step and give it to the scheduler of ``launch``.

        The checks, in this order: the step is waiting; the decision is declared by the step; the
        caller holds the title named by the argument ``role`` (``titles`` None: no title is
        asked for, the caller is an admin or the command line). Returns the commands the answer
        causes; the caller drives them after ``launch.commands``.
        """
        scheduler = launch.scheduler
        state = scheduler.states.get(step)
        if state is None:
            raise StepNotFoundError(step)
        if state is not StepState.WAITING:
            raise StepNotWaitingError(f"{step} is {state.value}, not waiting for an answer")
        args = scheduler.arguments(step)
        action = next(s.action for s in scheduler.plan.steps if s.id == step)
        allowed = self.declared_decisions(action, args) or ()
        if decision not in allowed:
            raise DecisionNotDeclaredError(decision, allowed)
        title = args.get("role")
        if titles is not None and isinstance(title, str) and title and title not in titles:
            raise TitleMissingError(title)
        return list(launch.commands) + scheduler.signal(step, {"decision": decision, "by": by}, now)

    def _scheduler(self, plan: Plan, inputs: Mapping[str, object], workers: int) -> Scheduler:
        """A scheduler whose priorities are the feasible critical path for these inputs."""
        feasible = priorities(plan, self.workflows.signatures, inputs=inputs).priority
        return Scheduler(
            plan, inputs, workers, compensation_planner=compensation_blockers, priorities=feasible
        )

    def report(self, run_id: str) -> RunReport:
        run = self.store.get(run_id)
        plan = self.workflows.get_plan(run.workflow, run.version)
        events = self.store.events(run_id)
        return report_from(run_id, run.workflow, run.version, events, [s.id for s in plan.steps])

    def list_runs(self, workflow: str | None = None) -> list[StoredRun]:
        return self.store.runs() if workflow is None else self.store.runs_of(workflow)
