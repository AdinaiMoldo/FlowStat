"""Move a run to another version and owner; finish the moves a stop left half-done (api-spec 10)."""

from __future__ import annotations

import logging
import sqlite3

from fastapi import Depends, Request

from flowstat.api.config import AppConfig
from flowstat.api.deps import get_db, get_runs
from flowstat.api.errors import ApiError, ErrorBody, ErrorItem, fail
from flowstat.api.models import Migrated, MigrateIn, Recovered
from flowstat.api.secured import SecuredRouter
from flowstat.migration.handover import OwnerUnavailableError
from flowstat.repo.ownership import EpochMismatchError, NotOwnerError, RunChangedError
from flowstat.repo.runs import RunNotFoundError
from flowstat.repo.versions import VersionNotFoundError
from flowstat.service.handover import RunBusyError
from flowstat.service.migration import SameVersionError
from flowstat.service.moving import HandoverService, NotMigratableError, TargetUnavailableError
from flowstat.service.runs import NotPublishedError, RunNotOwnedError, RunService
from flowstat.service.workflows import WorkflowService

log = logging.getLogger("flowstat.api")
router = SecuredRouter(tags=["migration"])
UNREACHABLE = {
    503: {"model": ErrorBody, "description": "an owner of the run cannot be reached (E-MIG-011)"}
}


def service_of(request: Request, db: sqlite3.Connection, runs: RunService) -> HandoverService:
    transport_for = getattr(request.app.state, "transport_for", None)  # tests put one in
    return HandoverService(db, runs, request.app.state.locks, transport_for)


@router.secured(
    "POST",
    "/runs/{run_id}/migrate",
    "runs:migrate",
    summary="Move an unfinished run to another version and owner",
    responses=UNREACHABLE,
    response_model_by_alias=True,
)
def migrate(
    run_id: str,
    body: MigrateIn,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    runs: RunService = Depends(get_runs),
) -> Migrated:
    config: AppConfig = request.app.state.config
    try:
        moved = service_of(request, db, runs).migrate(run_id, body.to, body.owner, config.time())
    except RunNotFoundError:
        raise fail(404, "E-API-002", f"there is no run {run_id}") from None
    except VersionNotFoundError:
        raise fail(404, "E-API-002", f"there is no version {body.to} of the workflow") from None
    except SameVersionError as error:
        raise fail(422, "E-API-001", str(error), "to") from None
    except (NotPublishedError, TargetUnavailableError, RunBusyError) as error:
        raise fail(409, "E-API-003", str(error)) from None
    except RunChangedError:
        raise fail(409, "E-API-003", f"the history of {run_id} changed; check it again") from None
    except (RunNotOwnedError, NotOwnerError, EpochMismatchError) as error:
        raise fail(409, "E-MIG-010", str(error)) from None
    except NotMigratableError as error:
        raise ApiError(
            409,
            [
                ErrorItem(code="E-API-003", message=str(error)),
                *[ErrorItem(code=r.code, message=r.message, field=r.step) for r in error.reasons],
            ],
        ) from None
    except OwnerUnavailableError as error:
        raise fail(
            503,
            "E-MIG-011",
            f"{error}; the move is not lost: POST /migrations/recover takes it forward",
        ) from None
    status, _ = runs.status(run_id)
    return Migrated(
        run_id=run_id,
        source=moved.source_version,
        to=body.to,
        owner=body.owner,
        epoch=moved.ownership.epoch,
        status=status,
    )


@router.secured(
    "POST",
    "/migrations/recover",
    "runs:migrate",
    summary="Take the moves that were interrupted forward",
)
def recover(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    runs: RunService = Depends(get_runs),
) -> Recovered:
    return Recovered(recovered=service_of(request, db, runs).recover())


def recover_on_start(config: AppConfig, locks: object) -> None:
    """A core that starts finishes the moves it left in its journal."""
    from flowstat.repo.db import connect

    db = connect(config.db_path)
    try:
        workflows = WorkflowService(db)
        done = HandoverService(db, RunService(db, workflows), locks).recover()  # type: ignore[arg-type]
        if done:
            log.info("moves taken forward at start: %s", done)
    except Exception:
        log.exception("the moves left in the journal could not be taken forward")
    finally:
        db.close()
