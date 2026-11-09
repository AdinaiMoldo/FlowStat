"""Deployment routes: turn a published version into a service (api-spec section 7)."""

from __future__ import annotations

import sqlite3

from fastapi import Depends, Request

from flowstat.api.deps import config_of, get_db, get_workflows
from flowstat.api.errors import ApiError, ErrorItem, fail
from flowstat.api.models import DeployedOut, DeployIn, DeploymentOut, Ingested, IngestIn
from flowstat.api.runs import latest_published
from flowstat.api.secured import SecuredRouter
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
from flowstat.service.runs import NotPublishedError
from flowstat.service.workflows import WorkflowService

router = SecuredRouter(tags=["deployments"])


def get_deployments(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    workflows: WorkflowService = Depends(get_workflows),
) -> DeploymentService:
    config = config_of(request)
    return DeploymentService(
        db, workflows, config.packager, config.launchers, config.bundles, config.ingest_url
    )


def deployment_out(row: Deployment, runs: int = 0) -> DeploymentOut:
    return DeploymentOut(
        id=row.id,
        workflow=row.workflow,
        version=row.version,
        kind=row.kind,
        status=row.status,
        endpoint=row.endpoint,
        detail=row.detail,
        created_at=row.created_at,
        updated_at=row.updated_at,
        runs=runs,
        last_event_at=row.last_event_at,
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
