"""Deployment routes: turn a published version into a service (api-spec section 7)."""

from __future__ import annotations

import logging
import sqlite3
from typing import Literal

from fastapi import Depends, Request

from flowstat.api.config import AppConfig
from flowstat.api.deps import config_of, get_db, get_workflows
from flowstat.api.errors import ApiError, ErrorBody, ErrorItem, fail
from flowstat.api.models import (
    DeployedOut,
    DeployIn,
    DeploymentOut,
    Ingested,
    IngestIn,
    KeyRecoveryOut,
    KeyRotationOut,
)
from flowstat.api.runs import latest_published
from flowstat.api.secured import SecuredRouter
from flowstat.repo.db import connect
from flowstat.repo.deployments import Deployment, DeploymentNotFoundError
from flowstat.repo.versions import VersionNotFoundError
from flowstat.service.deployments import (
    DeploymentFailedError,
    DeploymentService,
    KindUnavailableError,
    LaunchError,
    NotRunningError,
)
from flowstat.service.ingest import (
    IngestConflictError,
    IngestInvalidError,
    IngestService,
    parse_batch,
)
from flowstat.service.rotation import KeyRotation, RotationRefusedError, ServiceAwayError
from flowstat.service.runs import NotPublishedError
from flowstat.service.workflows import WorkflowService

router = SecuredRouter(tags=["deployments"])
log = logging.getLogger("flowstat.api")


def get_deployments(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    workflows: WorkflowService = Depends(get_workflows),
) -> DeploymentService:
    config = config_of(request)
    return DeploymentService(
        db,
        workflows,
        config.packager,
        config.launchers,
        config.bundles,
        config.ingest_url,
        config.allowed_hosts,
    )


def deployment_out(row: Deployment, runs: int = 0) -> DeploymentOut:
    return DeploymentOut(
        id=row.id,
        workflow=row.workflow,
        version=row.version,
        kind=row.kind,
        status=row.state,
        endpoint=row.endpoint,
        detail=row.detail,
        created_at=row.created_at,
        updated_at=row.updated_at,
        runs=runs,
        last_event_at=row.last_event_at,
        answering=row.answering,
        answering_since=row.answering_since,
    )


@router.secured(
    "POST",
    "/deployments",
    "deployments:write",
    status_code=201,
    summary="Deploy a published version as a service",
)
def deploy(
    body: DeployIn,
    request: Request,
    deployments: DeploymentService = Depends(get_deployments),
    workflows: WorkflowService = Depends(get_workflows),
) -> DeployedOut:
    config = config_of(request)
    number = (
        body.version if body.version is not None else latest_published(workflows, body.workflow)
    )
    try:
        row, keys = deployments.deploy(
            body.workflow, number, body.kind, config.new_id, config.new_token, config.clock()
        )
    except VersionNotFoundError:
        raise fail(404, "E-API-002", f"there is no {body.workflow} v{number}") from None
    except NotPublishedError as error:
        raise fail(409, "E-API-003", str(error)) from None
    except KindUnavailableError as error:
        raise fail(409, "E-API-003", str(error)) from None
    except DeploymentFailedError as error:
        raise fail(409, "E-API-003", f"the service did not start: {error}") from None
    except ValueError as error:
        raise fail(422, "E-API-001", str(error), "kind") from None
    return DeployedOut(
        **deployment_out(row).model_dump(),
        api_key=keys.api_key,
        title_keys=dict(keys.title_keys),
        ingest_key=keys.ingest_key,
    )


@router.secured("GET", "/deployments", "deployments:read", summary="List deployments")
def list_deployments(
    deployments: DeploymentService = Depends(get_deployments),
) -> list[DeploymentOut]:
    counts = deployments.shipped()
    return [deployment_out(row, counts.get(row.id, 0)) for row in deployments.all()]


@router.secured("GET", "/deployments/{deployment_id}", "deployments:read", summary="One deployment")
def read_deployment(
    deployment_id: str, deployments: DeploymentService = Depends(get_deployments)
) -> DeploymentOut:
    try:
        return deployment_out(
            deployments.get(deployment_id), deployments.shipped().get(deployment_id, 0)
        )
    except DeploymentNotFoundError:
        raise fail(404, "E-API-002", f"there is no deployment {deployment_id}") from None


@router.secured(
    "DELETE", "/deployments/{deployment_id}", "deployments:write", summary="Stop a deployment"
)
def stop_deployment(
    deployment_id: str,
    request: Request,
    deployments: DeploymentService = Depends(get_deployments),
) -> DeploymentOut:
    try:
        row = deployments.stop(deployment_id, config_of(request).clock())
        return deployment_out(row, deployments.shipped().get(deployment_id, 0))
    except DeploymentNotFoundError:
        raise fail(404, "E-API-002", f"there is no deployment {deployment_id}") from None
    except (NotRunningError, KindUnavailableError) as error:
        raise fail(409, "E-API-003", str(error)) from None
    except LaunchError as error:
        raise fail(409, "E-API-003", f"the service could not be stopped: {error.detail}") from None


def get_ingest(db: sqlite3.Connection = Depends(get_db)) -> IngestService:
    return IngestService(db)


@router.secured(
    "POST",
    "/deployments/{deployment_id}/events",
    "events:ingest",
    summary="A deployed service ships the events of a run",
)
def ingest_events(
    deployment_id: str,
    body: IngestIn,
    request: Request,
    ingest: IngestService = Depends(get_ingest),
) -> Ingested:
    """Authenticated by the ingest key of this deployment (not by a user's token). At least once:
    events the core already has are ignored, a gap or a different event refuses the batch."""
    deployment = request.state.deployment
    try:
        batch = parse_batch(body.model_dump())
        last = ingest.accept(deployment, batch, config_of(request).clock())
    except IngestInvalidError as error:
        raise fail(422, "E-API-001", str(error)) from None
    except IngestConflictError as error:
        raise ApiError(
            409,
            [ErrorItem(code="E-API-003", message=str(error), expected=error.expected)],
        ) from None
    return Ingested(run_id=batch.run.run_id, stored_through=last)


AWAY = {
    503: {
        "model": ErrorBody,
        "description": "the service does not answer (E-KEY-001); the rotation is recorded and "
        "POST /deployments/{id}/keys/recover finishes it or takes it back",
    }
}


def keys_of(request: Request, db: sqlite3.Connection) -> KeyRotation:
    return KeyRotation(db, getattr(request.app.state, "key_transport_for", None))


@router.secured(
    "POST",
    "/deployments/{deployment_id}/keys/{kind}/rotate",
    "deployments:write",
    summary="Replace the control key or the ingest key of a service",
    responses=AWAY,
)
def rotate_key(
    deployment_id: str,
    kind: Literal["control", "ingest"],
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
) -> KeyRotationOut:
    config = config_of(request)
    try:
        keys_of(request, db).rotate(deployment_id, kind, config.new_token(), config.clock())
    except DeploymentNotFoundError:
        raise fail(404, "E-API-002", f"there is no deployment {deployment_id}") from None
    except RotationRefusedError as error:
        raise fail(409, "E-API-003", str(error)) from None
    except ServiceAwayError as error:
        raise fail(
            503, "E-KEY-001", f"{error}; POST /deployments/{deployment_id}/keys/recover ends it"
        ) from None
    return KeyRotationOut(deployment_id=deployment_id, key=kind, state="rotated")


@router.secured(
    "POST",
    "/deployments/{deployment_id}/keys/recover",
    "deployments:write",
    summary="Finish or take back a key rotation that was interrupted",
)
def recover_keys(
    deployment_id: str, request: Request, db: sqlite3.Connection = Depends(get_db)
) -> KeyRecoveryOut:
    try:
        outcome = keys_of(request, db).recover(deployment_id, config_of(request).clock())
    except DeploymentNotFoundError:
        raise fail(404, "E-API-002", f"there is no deployment {deployment_id}") from None
    return KeyRecoveryOut(deployment_id=deployment_id, outcome=outcome)


def recover_keys_on_start(config: AppConfig) -> None:
    """A core that starts finishes the rotations it left half done."""
    db = connect(config.db_path)
    try:
        done = KeyRotation(db).recover_all(config.clock())
        if done:
            log.info("key rotations taken to their end at start: %s", done)
    except Exception:
        log.exception("the key rotations left in the registry could not be looked at")
    finally:
        db.close()
