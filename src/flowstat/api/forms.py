"""The regional forms: receive, list and remove (api-spec section 11)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from fastapi import Depends, Query, Request, Response
from fastapi.concurrency import run_in_threadpool

from flowstat.api.deps import config_of, get_db
from flowstat.api.errors import fail
from flowstat.api.models import FormOut, FormsOut
from flowstat.api.secured import SecuredRouter
from flowstat.repo.campaigns import CampaignRepo
from flowstat.repo.catalogue import CatalogueRepo
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

    return FormService(config_of(request).data_dir, CatalogueRepo(db), locking)


def out_of(info: FormInfo) -> FormOut:
    return FormOut(
        region=info.region,
        name=info.name,
        quarter=info.quarter,
        received=info.received,
        rows=info.rows,
        size=info.size,
        problems=list(info.problems),
    )


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
    found = await work(lambda: service_of(request, db).save(region, quarter, content))
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
    await work(lambda: service_of(request, db).remove(region, quarter))
    return Response(status_code=204)
