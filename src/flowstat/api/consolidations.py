"""The results of a campaign (api-spec section 14): make one, list them, read one with the
figures each total comes from."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from fastapi import Depends, Request
from fastapi.concurrency import run_in_threadpool

from flowstat.api.deps import config_of, get_db
from flowstat.api.errors import fail
from flowstat.api.models import (
    ConsolidationDetailOut,
    ConsolidationIn,
    ConsolidationOut,
    ConsolidationsOut,
    IncludedOut,
    LeftOutOut,
    LineOut,
    LinePartOut,
    ResultDecisionIn,
    ResultDecisionOut,
)
from flowstat.api.secured import SecuredRouter
from flowstat.model.campaign import CampaignError
from flowstat.model.consolidation import ConsolidationError
from flowstat.model.rules import plain
from flowstat.repo.campaigns import CampaignNotFoundError, CampaignRepo
from flowstat.repo.consolidations import ConsolidationNotFoundError, Decision, Stored
from flowstat.service.consolidations import ConsolidationService

router = SecuredRouter(tags=["consolidations"])

STATUS_OF = {
    "E-CONS-001": 409,
    "E-CONS-002": 409,
    "E-CONS-003": 409,
    "E-CONS-004": 422,
    "E-CONS-005": 409,
    "E-CONS-006": 409,
    "E-CONS-007": 409,
    "E-CONS-008": 422,
}


def service_of(request: Request, db: sqlite3.Connection) -> ConsolidationService:
    return ConsolidationService(db, config_of(request).clock)


def summary(found: Stored, period: str, decision: Decision | None = None) -> dict[str, Any]:
    r = found.result
    return {
        "decision": None
        if decision is None
        else ResultDecisionOut(
            decision=decision.decision, comment=decision.comment, by=decision.by, at=decision.at
        ),
        "campaign": found.campaign_id,
        "period": period,
        "number": found.number,
        "kind": r.kind,
        "complete": r.complete,
        "count": r.count,
        "expected": r.expected,
        "share": r.share,
        "included": [IncludedOut(region=a, version=b, sha256=c) for a, b, c in r.included],
        "left_out": [
            LeftOutOut(region=o.region, reason=o.reason, detail=o.detail) for o in r.left_out
        ],
        "made_by": found.made_by,
        "made_at": found.made_at,
        "stale": found.stale_at is not None,
        "stale_at": found.stale_at,
        "stale_reason": found.stale_reason,
    }


def detail(found: Stored, period: str, decision: Decision | None = None) -> ConsolidationDetailOut:
    lines = [
        LineOut(
            code=ln.code,
            name=ln.name,
            total=plain(ln.total),
            parts=[LinePartOut(region=r, version=v, value=plain(x)) for r, v, x in ln.parts],
        )
        for ln in found.result.lines
    ]
    return ConsolidationDetailOut(**summary(found, period, decision), lines=lines)


async def work(call: Callable[[], Any]) -> Any:
    try:
        return await run_in_threadpool(call)
    except ConsolidationError as error:
        raise fail(STATUS_OF.get(error.code, 409), error.code, str(error)) from None
    except CampaignError as error:
        raise fail(409, error.code, str(error)) from None
    except CampaignNotFoundError:
        raise fail(404, "E-API-002", "there is no such campaign") from None
    except ConsolidationNotFoundError:
        raise fail(404, "E-API-002", "the campaign has no such result") from None


def period_of(db: sqlite3.Connection, campaign_id: str) -> str:
    return CampaignRepo(db).get(campaign_id).settings.period


@router.secured(
    "POST",
    "/campaigns/{campaign_id}/consolidations",
    "consolidations:write",
    status_code=201,
    summary="Make a preliminary or a final result from the valid forms of the campaign",
)
async def make_consolidation(
    campaign_id: str,
    body: ConsolidationIn,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
) -> ConsolidationDetailOut:
    def make() -> ConsolidationDetailOut:
        found = service_of(request, db).make(campaign_id, body.kind, request.state.user.name)
        return detail(found, period_of(db, campaign_id))

    made: ConsolidationDetailOut = await work(make)
    return made


@router.secured(
    "GET",
    "/campaigns/{campaign_id}/consolidations",
    "campaigns:read",
    summary="The results of a campaign, the newest first",
)
async def list_consolidations(
    campaign_id: str, request: Request, db: sqlite3.Connection = Depends(get_db)
) -> ConsolidationsOut:
    def read() -> ConsolidationsOut:
        service = service_of(request, db)
        found = service.all(campaign_id)
        period = period_of(db, campaign_id)
        taken = service.decisions(campaign_id)
        return ConsolidationsOut(
            results=[ConsolidationOut(**summary(f, period, taken.get(f.number))) for f in found]
        )

    listed: ConsolidationsOut = await work(read)
    return listed


@router.secured(
    "GET",
    "/campaigns/{campaign_id}/consolidations/{number}",
    "campaigns:read",
    summary="One result, with the figures that each total was added from",
)
async def get_consolidation(
    campaign_id: str, number: int, request: Request, db: sqlite3.Connection = Depends(get_db)
) -> ConsolidationDetailOut:
    def read() -> ConsolidationDetailOut:
        service = service_of(request, db)
        found = service.get(campaign_id, number)
        taken = service.decisions(campaign_id).get(number)
        return detail(found, period_of(db, campaign_id), taken)

    one: ConsolidationDetailOut = await work(read)
    return one


@router.secured(
    "POST",
    "/campaigns/{campaign_id}/consolidations/{number}/decision",
    "consolidations:decide",
    summary="Approve the final result, reject it, or ask the regions for a correction",
)
async def decide_consolidation(
    campaign_id: str,
    number: int,
    body: ResultDecisionIn,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
) -> ConsolidationDetailOut:
    user = request.state.user

    def decide() -> ConsolidationDetailOut:
        service = service_of(request, db)
        campaign = CampaignRepo(db).get(campaign_id)
        service.get(campaign_id, number)  # 404 before anything is said about titles
        title = campaign.settings.approver
        if user.role != "admin" and title not in user.titles:  # an admin needs no title
            raise fail(403, "E-AUTH-002", f"the decision needs the title {title}")
        found = service.decide(campaign_id, number, body.decision, body.comment, user.name)
        taken = service.decisions(campaign_id).get(number)
        return detail(found, campaign.settings.period, taken)

    made: ConsolidationDetailOut = await work(decide)
    return made
