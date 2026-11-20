"""The runs of the workflow of a campaign (api-spec section 15): start one, list them."""

from __future__ import annotations

import sqlite3

from fastapi import Depends, Request

from flowstat.api.deps import config_of, get_db, get_runs, get_workflows
from flowstat.api.errors import fail
from flowstat.api.models import CampaignRunOut, CampaignRunsOut, Started, StartIn
from flowstat.api.runs import start_run
from flowstat.api.secured import SecuredRouter
from flowstat.model import campaign as life
from flowstat.model.campaign import Campaign
from flowstat.repo.campaigns import CampaignNotFoundError, CampaignRepo
from flowstat.repo.form_versions import FormVersionRepo
from flowstat.repo.reuse import ReuseRepo
from flowstat.repo.runs import RunNotFoundError
from flowstat.service.runs import RunService
from flowstat.service.workflows import WorkflowService

router = SecuredRouter(tags=["campaigns"])

# The workflow runs while the campaign is at work: after it was opened for collecting and before
# it is closed.
RUNS_IN = (
    life.COLLECTING,
    life.VALIDATING,
    life.READY,
    life.PRELIMINARY,
    life.PENDING,
    life.APPROVED,
)


def campaign_of(db: sqlite3.Connection, campaign_id: str) -> Campaign:
    try:
        return CampaignRepo(db).get(campaign_id)
    except CampaignNotFoundError:
        raise fail(404, "E-API-002", "there is no such campaign") from None


@router.secured(
    "POST",
    "/campaigns/{campaign_id}/runs",
    "runs:start",
    status_code=202,
    summary="Run the workflow of the campaign for its quarter, with the forms as they are now",
)
def start_campaign_run(
    campaign_id: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    workflows: WorkflowService = Depends(get_workflows),
    runs: RunService = Depends(get_runs),
) -> Started:
    campaign = campaign_of(db, campaign_id)
    s = campaign.settings
    if campaign.status not in RUNS_IN:
        raise fail(
            409,
            "E-CAMP-009",
            f"the workflow of a campaign runs from {life.COLLECTING} to {life.APPROVED};"
            f" the campaign is {campaign.status}",
        )
    versions = FormVersionRepo(db)
    forms: dict[str, int] = {}
    for region in s.regions:
        current = versions.current(region, s.period)
        if current is not None:
            forms[region] = current.number
    config = config_of(request)
    by = str(request.state.user.name)

    def stored(run_id: str) -> None:
        ReuseRepo(db).add_run(run_id, campaign_id, by, config.clock(), forms)

    body = StartIn(version=s.workflow_version, inputs={"quarter": s.period})
    return start_run(request, workflows, runs, s.workflow, body, stored)


@router.secured(
    "GET",
    "/campaigns/{campaign_id}/runs",
    "campaigns:read",
    summary="The runs of the workflow of a campaign, the newest first",
)
def list_campaign_runs(
    campaign_id: str,
    db: sqlite3.Connection = Depends(get_db),
    runs: RunService = Depends(get_runs),
) -> CampaignRunsOut:
    campaign_of(db, campaign_id)
    repo = ReuseRepo(db)
    out: list[CampaignRunOut] = []
    for found in repo.runs_of(campaign_id):
        try:
            report = runs.report(found.run_id)
        except RunNotFoundError:  # the run was moved to another owner; the campaign still lists it
            status, outcome = "MOVED", None
        else:
            status = report.status
            outcome = report.outcome.value if report.outcome is not None else None
        uses = repo.uses(found.run_id)
        out.append(
            CampaignRunOut(
                run_id=found.run_id,
                started_by=found.started_by,
                started_at=found.started_at,
                status=status,
                outcome=outcome,
                forms=found.forms,
                executed=sum(1 for u in uses if not u.reused),
                reused=sum(1 for u in uses if u.reused),
                executed_steps=[u.step for u in uses if not u.reused],
                reused_steps=[u.step for u in uses if u.reused],
            )
        )
    return CampaignRunsOut(runs=out)
