"""Run routes: start a run, read it, list runs, answer an approval (api-spec section 5).

A run is driven in the background by the app's pool (``AppConfig.submit``), one thread at a time
per run (``RunLocks``). The request checks everything it can and answers 202; the job opens its own
connection, because a connection belongs to one thread.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable

from fastapi import Depends, Request

from flowstat.actions.registry import Registry
from flowstat.api.config import AppConfig
from flowstat.api.deps import config_of, get_runs, get_workflows
from flowstat.api.errors import ApiError, ErrorItem, fail
from flowstat.api.locks import RunLocks
from flowstat.api.models import (
    CoreRunSummary,
    Decided,
    DecisionIn,
    RunOut,
    RunSummary,
    Started,
    StartIn,
    StepOut,
)
from flowstat.api.secured import SecuredRouter
from flowstat.engine.scheduler import Command
from flowstat.model.history import RunReport
from flowstat.model.versions import VersionStatus
from flowstat.repo.db import connect
from flowstat.repo.runs import RunNotFoundError
from flowstat.repo.users import User
from flowstat.repo.versions import VersionNotFoundError
from flowstat.service.driving import Machine, drive
from flowstat.service.runs import (
    DecisionNotDeclaredError,
    InvalidInputsError,
    Launch,
    NotPublishedError,
    RunNotOwnedError,
    RunService,
    StepNotFoundError,
    StepNotWaitingError,
    TitleMissingError,
)
from flowstat.service.workflows import WorkflowService

log = logging.getLogger("flowstat.api")
router = SecuredRouter(tags=["runs"])  # the core only: start a run of any workflow
shared = SecuredRouter(tags=["runs"])  # the same routes in the core and in a deployed service

Prepare = Callable[[RunService], tuple[Launch, list[Command] | None]]


def locks_of(request: Request) -> RunLocks:
    locks: RunLocks = request.app.state.locks
    return locks


def registry_of(request: Request) -> Registry:
    registry: Registry = request.app.state.registry
    return registry


def report_out(report: RunReport) -> RunOut:
    return RunOut(
        run_id=report.run_id,
        workflow=report.workflow,
        version=report.version,
        status=report.status,
        outcome=report.outcome.value if report.outcome is not None else None,
        started_at=report.started_at,
        finished_at=report.finished_at,
        steps=[
            StepOut(
                step=s.step,
                state=s.state.value,
                started_at=s.started_at,
                finished_at=s.finished_at,
                code=s.code,
                message=s.message,
                output=s.output,
                attempts=s.attempts,
                compensated_at=s.compensated_at,
            )
            for s in report.steps
        ],
    )


def drive_in_background(
    config: AppConfig, registry: Registry, locks: RunLocks, run_id: str, prepare: Prepare
) -> None:
    """Drive the run on the pool. The caller holds the lock of the run; the job releases it."""

    def job() -> None:
        db: sqlite3.Connection | None = None
        try:
            db = connect(config.db_path)
            workflows = WorkflowService(
                db,
                known_actions=registry.names(),
                signatures=registry.signatures(),
                plans=registry.plans,
            )
            runs = RunService(db, workflows)
            launch, commands = prepare(runs)
            machine = Machine(
                config.data_dir,
                config.target,
                config.workers,
                config.time,
                config.sleep,
                config.allowed_hosts,
            )
            drive(runs, run_id, launch, machine, commands)
        except Exception:  # the log keeps what happened; the next start takes the run up
            log.exception("the run %s stopped with an error", run_id)
        finally:
            if db is not None:
                db.close()
            locks.release(run_id)

    try:
        config.submit(job)
    except BaseException:
        locks.release(run_id)
        raise


def start_run(
    request: Request, workflows: WorkflowService, runs: RunService, name: str, body: StartIn
) -> Started:
    """Check, store and start a run of ``name``; shared by the core and the service."""
    config = config_of(request)
    number = body.version if body.version is not None else latest_published(workflows, name)
    run_id = config.new_id()
    try:
        runs.start(run_id, name, number, body.inputs, config.clock())
    except VersionNotFoundError:
        raise fail(404, "E-API-002", f"there is no {name} v{number}") from None
    except NotPublishedError as error:
        raise fail(409, "E-API-003", str(error)) from None
    except InvalidInputsError as error:
        raise ApiError(
            422,
            [
                ErrorItem(code=e.code, message=e.message, field=f"inputs.{e.input}")
                for e in error.errors
            ],
        ) from None
    locks = locks_of(request)
    locks.try_acquire(run_id)  # the id is new, so the lock is free
    drive_in_background(
        config,
        registry_of(request),
        locks,
        run_id,
        lambda r: (r.launch(run_id, config.workers), None),
    )
    return Started(run_id=run_id, workflow=name, version=number, status="CREATED")


def latest_published(workflows: WorkflowService, name: str) -> int:
    versions = workflows.versions(name)
    if not versions:
        raise fail(404, "E-API-002", f"there is no workflow '{name}'")
    published = [v.number for v in versions if v.status is VersionStatus.PUBLISHED]
    if not published:
        raise fail(409, "E-API-003", f"'{name}' has no published version; publish it first")
    return published[-1]


def answer_run(request: Request, runs: RunService, run_id: str, body: DecisionIn) -> Decided:
    """Check an answer to an approval and drive the run on; shared by the core and the service."""
    config, registry, locks = config_of(request), registry_of(request), locks_of(request)
    user: User = request.state.user
    titles = None if user.role == "admin" else user.titles  # an admin needs no title

    def prepare(service: RunService) -> tuple[Launch, list[Command] | None]:
        launch = service.recover(run_id, config.workers, config.time(), registry.is_idempotent)
        given = service.answer(launch, body.step, body.decision, user.name, titles, config.time())
        return launch, [*launch.commands, *given]

    if not locks.try_acquire(run_id):
        raise fail(409, "E-API-003", f"the run {run_id} is being driven right now")
    try:
        prepare(runs)  # every check, with nothing stored
    except RunNotFoundError:
        locks.release(run_id)
        raise fail(404, "E-API-002", f"there is no run {run_id}") from None
    except RunNotOwnedError as error:
        locks.release(run_id)
        raise fail(409, "E-MIG-010", str(error)) from None
    except StepNotFoundError:
        locks.release(run_id)
        raise fail(404, "E-API-002", f"the run {run_id} has no step '{body.step}'") from None
    except StepNotWaitingError as error:
        locks.release(run_id)
        raise fail(409, "E-API-003", str(error)) from None
    except DecisionNotDeclaredError as error:
        locks.release(run_id)
        raise fail(422, "E-API-005", str(error), "decision") from None
    except TitleMissingError as error:
        locks.release(run_id)
        raise fail(403, "E-AUTH-002", str(error)) from None
    except BaseException:
        locks.release(run_id)
        raise
    drive_in_background(config, registry, locks, run_id, prepare)
    return Decided(run_id=run_id, step=body.step, decision=body.decision, by=user.name)


def read_run(runs: RunService, run_id: str) -> RunOut:
    try:
        return report_out(runs.report(run_id))
    except RunNotFoundError:
        raise fail(404, "E-API-002", f"there is no run {run_id}") from None


def list_runs(
    runs: RunService,
    workflow: str | None,
    status: str | None,
    deployment: str | None = None,
) -> list[CoreRunSummary]:
    """The runs, newest first; ``deployment`` keeps those that service shipped to the core."""
    found = []
    for stored in sorted(
        runs.list_runs(workflow), key=lambda r: (r.created_at, r.run_id), reverse=True
    ):
        if deployment is not None and stored.origin != deployment:
            continue
        run_status, outcome = runs.status(stored.run_id)
        if status is None or run_status == status:
            found.append(
                CoreRunSummary(
                    run_id=stored.run_id,
                    workflow=stored.workflow,
                    version=stored.version,
                    status=run_status,
                    outcome=outcome.value if outcome is not None else None,
                    created_at=stored.created_at,
                    origin=stored.origin,
                )
            )
    return found


def plain(summary: CoreRunSummary) -> RunSummary:
    """The summary a deployed service gives: it has no origin to tell."""
    return RunSummary(**summary.model_dump(exclude={"origin"}))


@router.secured(
    "POST",
    "/workflows/{name}/runs",
    "runs:start",
    status_code=202,
    summary="Start a run of a published version",
)
def start(
    name: str,
    body: StartIn,
    request: Request,
    workflows: WorkflowService = Depends(get_workflows),
    runs: RunService = Depends(get_runs),
) -> Started:
    return start_run(request, workflows, runs, name, body)


@router.secured("GET", "/runs", "runs:read", summary="List runs, newest first")
def runs_list(
    workflow: str | None = None,
    status: str | None = None,
    deployment: str | None = None,
    runs: RunService = Depends(get_runs),
) -> list[CoreRunSummary]:
    return list_runs(runs, workflow, status, deployment)


@shared.secured("GET", "/runs/{run_id}", "runs:read", summary="The report of a run")
def run_report(run_id: str, runs: RunService = Depends(get_runs)) -> RunOut:
    return read_run(runs, run_id)


@shared.secured(
    "POST",
    "/runs/{run_id}/decisions",
    "runs:decide",
    status_code=202,
    summary="Answer a waiting approval",
)
def decide(
    run_id: str, body: DecisionIn, request: Request, runs: RunService = Depends(get_runs)
) -> Decided:
    return answer_run(request, runs, run_id, body)
