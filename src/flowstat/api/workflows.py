"""Workflow routes: validate, drafts, versions, publication, the catalogue (api-spec section 6)."""

from __future__ import annotations

from fastapi import Depends, Query, Request

from flowstat.api.deps import config_of, get_runs, get_workflows
from flowstat.api.errors import ApiError, ErrorItem, fail
from flowstat.api.models import (
    CatalogueIn,
    CatalogueSaved,
    Checked,
    CompleteIn,
    CompletionOut,
    DiagnosticOut,
    DraftSaved,
    GraphOut,
    MigrationOut,
    MigrationRunOut,
    Previewed,
    Published,
    ReasonOut,
    TextIn,
    VersionDetail,
    VersionGraph,
    VersionSummary,
    WorkflowSummary,
)
from flowstat.api.secured import SecuredRouter
from flowstat.model.diagnostics import Diagnostic
from flowstat.model.versions import VersionStatus
from flowstat.repo.versions import AlreadyPublishedError, VersionNotFoundError
from flowstat.service.migration import MigrationService, SameVersionError
from flowstat.service.permissions import PUBLIC
from flowstat.service.runs import NotPublishedError, RunService
from flowstat.service.workflows import InvalidWorkflowError, PublishError, WorkflowService

router = SecuredRouter(tags=["workflows"])


def _out(items: list[Diagnostic]) -> list[DiagnosticOut]:
    return [DiagnosticOut.of(d) for d in items]


def _not_found(name: str, number: int | None = None) -> ApiError:
    what = f"the workflow '{name}'" if number is None else f"{name} v{number}"
    return fail(404, "E-API-002", f"there is no {what}")


@router.secured("GET", "/health", PUBLIC, summary="Liveness check")
def health(request: Request) -> dict[str, str]:
    # a core that was started with demonstration sign-ins says so (docs/api-spec.md section 9)
    answer = {"status": "ok"}
    if config_of(request).demo_logins is not None:
        answer["demo_logins"] = "yes"
    return answer


@router.secured("POST", "/validate", "workflows:write", summary="Check a text without storing it")
def validate(body: TextIn, workflows: WorkflowService = Depends(get_workflows)) -> Checked:
    errors, findings = workflows.check(body.text)
    return Checked(diagnostics=_out(errors), findings=_out(findings))


@router.secured(
    "POST",
    "/graph",
    "workflows:write",
    summary="The graph of a text, or its errors; stores nothing",
)
def preview(body: TextIn, workflows: WorkflowService = Depends(get_workflows)) -> Previewed:
    errors, graph = workflows.preview(body.text)
    return Previewed(
        diagnostics=_out(errors), graph=GraphOut.of(graph) if graph is not None else None
    )


@router.secured(
    "POST",
    "/complete",
    "workflows:write",
    summary="What can be written inside ${ at the cursor; stores nothing",
)
def complete(
    body: CompleteIn, workflows: WorkflowService = Depends(get_workflows)
) -> CompletionOut:
    return CompletionOut.of(workflows.complete(body.text, body.line, body.column))


@router.secured("GET", "/workflows", "workflows:read", summary="List the workflows")
def list_workflows(workflows: WorkflowService = Depends(get_workflows)) -> list[WorkflowSummary]:
    out = []
    for name in workflows.versions_store.names():
        versions = workflows.versions(name)
        published = [v.number for v in versions if v.status is VersionStatus.PUBLISHED]
        out.append(
            WorkflowSummary(
                name=name,
                latest=versions[-1].number,
                latest_published=published[-1] if published else None,
            )
        )
    return out


@router.secured(
    "GET", "/workflows/{name}/versions", "workflows:read", summary="List the versions of a workflow"
)
def list_versions(
    name: str, workflows: WorkflowService = Depends(get_workflows)
) -> list[VersionSummary]:
    versions = workflows.versions(name)
    if not versions:
        raise _not_found(name)
    return [
        VersionSummary(
            number=v.number,
            status=v.status.value,
            created_at=v.created_at,
            published_at=v.published_at,
        )
        for v in versions
    ]


@router.secured(
    "GET",
    "/workflows/{name}/versions/{n}",
    "workflows:read",
    summary="Read a version with its errors and findings",
)
def read_version(
    name: str, n: int, workflows: WorkflowService = Depends(get_workflows)
) -> VersionDetail:
    try:
        version = workflows.get(name, n)
        errors, findings = workflows.analyse(name, n)
    except VersionNotFoundError:
        raise _not_found(name, n) from None
    return VersionDetail(
        workflow=name,
        version=n,
        status=version.status.value,
        text=version.text,
        created_at=version.created_at,
        published_at=version.published_at,
        diagnostics=_out(errors),
        findings=_out(findings),
    )


@router.secured(
    "GET",
    "/workflows/{name}/versions/{n}/graph",
    "workflows:read",
    summary="The graph of a stored version",
)
def read_graph(
    name: str, n: int, workflows: WorkflowService = Depends(get_workflows)
) -> VersionGraph:
    try:
        return VersionGraph(graph=GraphOut.of(workflows.graph(name, n)))
    except VersionNotFoundError:
        raise _not_found(name, n) from None
    except InvalidWorkflowError as error:
        summary = ErrorItem(code="E-API-004", message=f"{name} v{n} has errors and has no graph")
        details = [
            ErrorItem(code=d.code, message=d.message, line=d.line, column=d.column)
            for d in error.diagnostics
        ]
        raise ApiError(422, [summary, *details]) from None


@router.secured(
    "POST",
    "/workflows/{name}/versions",
    "workflows:write",
    status_code=201,
    summary="Save a draft (it gets the next number)",
)
def save_draft(
    name: str,
    body: TextIn,
    request: Request,
    workflows: WorkflowService = Depends(get_workflows),
) -> DraftSaved:
    saved = workflows.save_draft(name, body.text, config_of(request).clock())
    return DraftSaved(
        workflow=name,
        version=saved.version.number,
        status=saved.version.status.value,
        diagnostics=_out(saved.diagnostics),
        findings=_out(saved.findings),
    )


@router.secured(
    "POST",
    "/workflows/{name}/versions/{n}/publish",
    "workflows:publish",
    summary="Publish a draft: from then on it never changes",
)
def publish(
    name: str, n: int, request: Request, workflows: WorkflowService = Depends(get_workflows)
) -> Published:
    try:
        version = workflows.publish(name, n, config_of(request).clock())
    except VersionNotFoundError:
        raise _not_found(name, n) from None
    except AlreadyPublishedError:
        raise fail(409, "E-API-003", f"{name} v{n} is already published") from None
    except PublishError as error:
        summary = ErrorItem(
            code="E-API-004",
            message=f"{name} v{n} has errors and was not published",
        )
        details = [
            ErrorItem(code=d.code, message=d.message, line=d.line, column=d.column)
            for d in error.diagnostics
        ]
        raise ApiError(422, [summary, *details]) from None
    _, findings = workflows.analyse(name, n)
    return Published(
        workflow=name,
        version=n,
        status=version.status.value,
        published_at=version.published_at,
        findings=_out(findings),
    )


@router.secured(
    "PUT",
    "/catalogue/{list_name}",
    "catalogue:write",
    summary="Replace a list of the reference catalogue",
)
def put_catalogue(
    list_name: str, body: CatalogueIn, workflows: WorkflowService = Depends(get_workflows)
) -> CatalogueSaved:
    codes = [item.get("code", "") for item in body.items]
    if not all(codes):
        raise fail(422, "E-API-001", "every item needs a 'code'", "items")
    if len(set(codes)) != len(codes):
        raise fail(422, "E-API-001", "the codes of the items must be different", "items")
    workflows.set_catalogue_list(list_name, body.items)
    return CatalogueSaved(list=list_name, count=len(body.items))


@router.secured(
    "GET",
    "/workflows/{name}/migration",
    "runs:read",
    response_model_by_alias=True,
    summary="Which unfinished runs of one version can move to another",
)
def migration(
    name: str,
    source: int = Query(alias="from"),
    target: int = Query(alias="to"),
    runs: RunService = Depends(get_runs),
) -> MigrationOut:
    try:
        checks = MigrationService(runs).report(name, source, target)
    except SameVersionError as error:
        raise fail(422, "E-API-001", str(error), "to") from None
    except VersionNotFoundError as error:
        raise fail(404, "E-API-002", f"there is no {error}") from None
    except NotPublishedError as error:
        raise fail(409, "E-API-003", str(error)) from None
    return MigrationOut(
        workflow=name,
        source=source,
        target=target,
        runs=[
            MigrationRunOut(
                run_id=c.run_id,
                owner=c.owner,
                status=c.status,
                migratable=c.verdict.migratable,
                reasons=[
                    ReasonOut(code=r.code, step=r.step, message=r.message)
                    for r in c.verdict.reasons
                ],
                kept=list(c.verdict.kept),
                added=list(c.verdict.added),
                pending=list(c.verdict.pending),
                dropped=list(c.verdict.dropped),
            )
            for c in checks
        ],
    )
