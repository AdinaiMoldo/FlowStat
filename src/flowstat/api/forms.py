"""The regional forms: receive, list and remove (api-spec section 11)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from fastapi import Depends, Query, Request, Response
from fastapi.concurrency import run_in_threadpool

from flowstat.api.deps import config_of, get_db
from flowstat.api.errors import fail
from flowstat.api.models import (
    FindingOut,
    FormComparisonOut,
    FormOut,
    FormsOut,
    FormVersionDetailOut,
    FormVersionOut,
    FormVersionsOut,
)
from flowstat.api.secured import SecuredRouter
from flowstat.model.campaign import ARCHIVED
from flowstat.model.form_versions import FormVersion
from flowstat.model.rules import Finding
from flowstat.repo.campaigns import CampaignRepo
from flowstat.repo.catalogue import CatalogueRepo
from flowstat.repo.form_versions import FormVersionRepo
from flowstat.service.forms import (
    MAX_BYTES,
    FormInfo,
    FormLocked,
    FormNotFound,
    FormRefused,
    FormService,
)

router = SecuredRouter(tags=["forms"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
BODY = {"required": True, "content": {XLSX: {"schema": {"type": "string", "format": "binary"}}}}


def service_of(request: Request, db: sqlite3.Connection) -> FormService:
    campaigns = CampaignRepo(db)

    def locking(quarter: str) -> str | None:
        found = campaigns.locking(quarter)
        return None if found is None else found.settings.name

    def rules_of(quarter: str) -> str:
        # the rules of the campaign of the period that is still alive; the basic ones without one
        alive = [c for c in campaigns.of_period(quarter) if c.status != ARCHIVED]
        return alive[0].settings.rules if alive else "basic"

    return FormService(
        config_of(request).data_dir,
        CatalogueRepo(db),
        locking,
        rules_of,
        FormVersionRepo(db),
        config_of(request).clock,
    )


def by_of(request: Request) -> str:
    """The name of the user who is calling (set by the check of the permission)."""
    return str(request.state.user.name)


def finding_out(f: Finding) -> FindingOut:
    return FindingOut(
        code=f.code, severity=f.severity.value, message=f.message, region=f.region,
        file=f.file, sheet=f.sheet, row=f.row, cell=f.cell, indicator=f.indicator,
        actual=f.actual, expected=f.expected,
    )  # fmt: skip


def out_of(info: FormInfo) -> FormOut:
    return FormOut(
        region=info.region,
        name=info.name,
        quarter=info.quarter,
        received=info.received,
        rows=info.rows,
        size=info.size,
        problems=list(info.problems),
        findings=[finding_out(f) for f in info.findings],
        version=info.version,
        versions=info.versions,
        uploaded_by=info.uploaded_by,
        uploaded_at=info.uploaded_at,
    )


def version_out(v: FormVersion, latest: int) -> FormVersionOut:
    return FormVersionOut(
        version=v.number, uploaded_by=v.by, uploaded_at=v.at, size=v.size, rows=v.rows,
        rules=v.rules, errors=v.errors, warnings=v.warnings,
        current=v.number == latest and v.removed_at is None,
        removed_at=v.removed_at, removed_by=v.removed_by,
    )  # fmt: skip


def refused(error: FormNotFound | FormRefused | FormLocked) -> Exception:
    if isinstance(error, FormLocked):
        return fail(409, "E-CAMP-006", str(error))
    if isinstance(error, FormNotFound):
        return fail(404, "E-API-002", str(error))
    return fail(422, "E-FORM-001", str(error))


async def read_body(request: Request) -> bytes:
    """The body, at most ``MAX_BYTES``: the length that is announced and the length that comes are
    both checked, so that a caller cannot fill the memory of the core."""
    announced = request.headers.get("content-length", "")
    if announced.isdigit() and int(announced) > MAX_BYTES:
        raise fail(413, "E-FORM-002", f"a form may have at most {MAX_BYTES} bytes")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BYTES:
            raise fail(413, "E-FORM-002", f"a form may have at most {MAX_BYTES} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


async def work(call: Callable[[], Any]) -> Any:
    try:
        return await run_in_threadpool(call)
    except (FormNotFound, FormRefused, FormLocked) as error:
        raise refused(error) from None


@router.secured("GET", "/forms", "forms:read", summary="The forms of a quarter, region by region")
async def list_forms(
    request: Request,
    quarter: str = Query(..., description="for example 2025Q1"),
    db: sqlite3.Connection = Depends(get_db),
) -> FormsOut:
    found = await work(lambda: service_of(request, db).listing(quarter))
    return FormsOut(quarter=quarter, forms=[out_of(i) for i in found])


@router.secured(
    "PUT",
    "/forms/{region}/{quarter}",
    "forms:write",
    summary="Receive the form of a region for a quarter (an xlsx file as the body)",
    openapi_extra={"requestBody": BODY},
)
async def put_form(
    region: str, quarter: str, request: Request, db: sqlite3.Connection = Depends(get_db)
) -> FormOut:
    content = await read_body(request)
    found = await work(
        lambda: service_of(request, db).save(region, quarter, content, by_of(request))
    )
    return out_of(found)


@router.secured(
    "DELETE",
    "/forms/{region}/{quarter}",
    "forms:write",
    status_code=204,
    summary="Remove the form of a region for a quarter",
)
async def delete_form(
    region: str, quarter: str, request: Request, db: sqlite3.Connection = Depends(get_db)
) -> Response:
    await work(lambda: service_of(request, db).remove(region, quarter, by_of(request)))
    return Response(status_code=204)


@router.secured(
    "GET",
    "/forms/{region}/{quarter}/versions",
    "forms:read",
    summary="Every version that was kept of the form of a region, the newest first",
)
async def list_versions(
    region: str, quarter: str, request: Request, db: sqlite3.Connection = Depends(get_db)
) -> FormVersionsOut:
    kept = await work(lambda: service_of(request, db).history(region, quarter))
    latest = kept[0].number if kept else 0
    return FormVersionsOut(
        region=region, quarter=quarter, versions=[version_out(v, latest) for v in kept]
    )


@router.secured(
    "GET",
    "/forms/{region}/{quarter}/versions/{number}",
    "forms:read",
    summary="One version of a form, with what the rules found in it when it was sent",
)
async def get_version(
    region: str,
    quarter: str,
    number: int,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
) -> FormVersionDetailOut:
    service = service_of(request, db)
    found = await work(lambda: service.version(region, quarter, number))
    latest = (await work(lambda: service.history(region, quarter)))[0].number
    return FormVersionDetailOut(
        **version_out(found, latest).model_dump(),
        findings=[finding_out(f) for f in found.findings],
    )


@router.secured(
    "GET",
    "/forms/{region}/{quarter}/compare",
    "forms:read",
    summary="What a newer version of a form fixed, kept and brought, against an older one",
)
async def compare_versions(
    region: str,
    quarter: str,
    request: Request,
    older: int | None = Query(None, description="default: the one before `newer`"),
    newer: int | None = Query(None, description="default: the latest"),
    db: sqlite3.Connection = Depends(get_db),
) -> FormComparisonOut:
    first, second, result = await work(
        lambda: service_of(request, db).compare(region, quarter, older, newer)
    )
    return FormComparisonOut(
        region=region,
        quarter=quarter,
        older=first.number,
        newer=second.number,
        fixed=[finding_out(f) for f in result.fixed],
        remaining=[finding_out(f) for f in result.remaining],
        introduced=[finding_out(f) for f in result.introduced],
    )
