"""Reporting campaigns (api-spec section 12): create, read, edit while a draft, move on."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from fastapi import Depends, Request
from fastapi.concurrency import run_in_threadpool

from flowstat.api.deps import config_of, get_db
from flowstat.api.errors import fail
from flowstat.api.forms import service_of as forms_of
from flowstat.api.models import (
    CampaignIn,
    CampaignOut,
    ProgressOut,
    RegionProgressOut,
    TransitionIn,
)
from flowstat.api.secured import SecuredRouter
from flowstat.model import campaign as life
from flowstat.model.campaign import Campaign, CampaignError, Settings
from flowstat.repo.campaigns import CampaignNotFoundError
from flowstat.service.campaigns import CampaignService

router = SecuredRouter(tags=["campaigns"])

STATUS_OF = {
    "E-CAMP-001": 422,
    "E-CAMP-002": 409,
    "E-CAMP-003": 409,
    "E-CAMP-004": 409,
    "E-CAMP-005": 409,
    "E-CAMP-007": 409,
    "E-CAMP-008": 409,
}


def service_of(request: Request, db: sqlite3.Connection) -> CampaignService:
    config = config_of(request)
    return CampaignService(db, forms_of(request, db), config.new_id, config.clock)


def settings_of(body: CampaignIn) -> Settings:
    return Settings(
        name=body.name,
        year=body.year,
        quarter=body.quarter,
        regions=tuple(body.regions),
        deadline=body.deadline,
        workflow=body.workflow,
        workflow_version=body.workflow_version,
        template=body.template,
        rules=body.rules,
        preliminary_min=body.preliminary_min,
        final_min=body.final_min,
        approver=body.approver,
    )


def out_of(service: CampaignService, campaign: Campaign) -> CampaignOut:
    s = campaign.settings
    p = service.progress(campaign)
    assert s.preliminary_min is not None and s.final_min is not None
    return CampaignOut(
        id=campaign.id,
        name=s.name,
        year=s.year,
        quarter=s.quarter,
        period=s.period,
        regions=list(s.regions),
        deadline=s.deadline,
        workflow=s.workflow,
        workflow_version=s.workflow_version,
        template=s.template,
        rules=s.rules,
        preliminary_min=s.preliminary_min,
        final_min=s.final_min,
        approver=s.approver,
        status=campaign.status,
        next=life.manual_moves(campaign.status),
        created_by=campaign.created_by,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
        progress=ProgressOut(
            expected=p.expected,
            received=p.received,
            valid=p.valid,
            invalid=p.invalid,
            missing=list(p.missing),
            regions=[
                RegionProgressOut(
                    region=r.region, name=r.name, received=r.received, valid=r.valid,
                    problems=r.problems,
                )
                for r in p.regions
            ],
            preliminary_ready=p.preliminary_ready,
            final_ready=p.final_ready,
        ),
    )  # fmt: skip


async def work(call: Callable[[], Any]) -> Any:
    try:
        return await run_in_threadpool(call)
    except CampaignError as error:
        raise fail(STATUS_OF.get(error.code, 422), error.code, str(error), error.field) from None
    except CampaignNotFoundError:
        raise fail(404, "E-API-002", "there is no such campaign") from None


@router.secured(
    "POST", "/campaigns", "campaigns:write", status_code=201, summary="Create a campaign (a draft)"
)
async def create_campaign(
    body: CampaignIn, request: Request, db: sqlite3.Connection = Depends(get_db)
) -> CampaignOut:
    def make() -> CampaignOut:
        service = service_of(request, db)
        return out_of(service, service.create(settings_of(body), request.state.user.name))

    return await work(make)  # type: ignore[no-any-return]


@router.secured("GET", "/campaigns", "campaigns:read", summary="The campaigns, newest period first")
async def list_campaigns(
    request: Request, db: sqlite3.Connection = Depends(get_db)
) -> list[CampaignOut]:
    def read() -> list[CampaignOut]:
        service = service_of(request, db)
        return [out_of(service, c) for c in service.all()]

    return await work(read)  # type: ignore[no-any-return]


@router.secured("GET", "/campaigns/{campaign_id}", "campaigns:read", summary="One campaign")
async def get_campaign(
    campaign_id: str, request: Request, db: sqlite3.Connection = Depends(get_db)
) -> CampaignOut:
    def read() -> CampaignOut:
        service = service_of(request, db)
        return out_of(service, service.get(campaign_id))

    return await work(read)  # type: ignore[no-any-return]


@router.secured(
    "PUT",
    "/campaigns/{campaign_id}",
    "campaigns:write",
    summary="Replace the settings of a campaign that is still a draft",
)
async def put_campaign(
    campaign_id: str,
    body: CampaignIn,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
) -> CampaignOut:
    def change() -> CampaignOut:
        service = service_of(request, db)
        return out_of(service, service.update(campaign_id, settings_of(body)))

    return await work(change)  # type: ignore[no-any-return]


@router.secured(
    "POST",
    "/campaigns/{campaign_id}/transitions",
    "campaigns:write",
    summary="Move a campaign to the next status; only the changes a person may make are accepted",
)
async def transition_campaign(
    campaign_id: str,
    body: TransitionIn,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
) -> CampaignOut:
    def change() -> CampaignOut:
        service = service_of(request, db)
        return out_of(service, service.move(campaign_id, body.to, life.MANUAL))

    return await work(change)  # type: ignore[no-any-return]
