"""Make and keep the results of a campaign (US-57).

The arithmetic and the rules of who is in are ``model/consolidation.py``. This service gathers
what they need: for each region of the campaign the current version of its form, read from the
version that was kept and not from the file on the disk, and whether the content rules block it.
It keeps each result as a new number, moves the campaign (the system's changes of the life cycle),
and marks the results stale when a form of the campaign changes afterwards.
"""

from __future__ import annotations

import io
import sqlite3
from collections.abc import Callable

from flowstat.actions.base import ActionError
from flowstat.actions.xlsx import read_sheet
from flowstat.model import campaign as life
from flowstat.model import consolidation as model
from flowstat.model.campaign import Campaign, CampaignError
from flowstat.model.consolidation import ConsolidationError, Left, Part
from flowstat.model.rules import State, read_value
from flowstat.repo.campaigns import CampaignRepo
from flowstat.repo.consolidations import ConsolidationRepo, Stored
from flowstat.repo.form_versions import FormVersionRepo

# The statuses in which a result can be made, and the way from each to READY.
_FROM = {life.VALIDATING: (life.READY,), life.READY: (), life.PRELIMINARY: (life.READY,)}
_AFTER = {model.PRELIMINARY: life.PRELIMINARY, model.FINAL: life.PENDING}
# A result that somebody may be reading or approving: a changed form sends the campaign back.
_SHOWN = (life.PRELIMINARY, life.PENDING)


class ConsolidationService:
    def __init__(self, db: sqlite3.Connection, clock: Callable[[], str]) -> None:
        self.repo = ConsolidationRepo(db)
        self.campaigns = CampaignRepo(db)
        self.versions = FormVersionRepo(db)
        self.clock = clock

    # -- what the forms give ---------------------------------------------------------------------
    def _gather(self, campaign: Campaign) -> tuple[dict[str, Part], dict[str, Left]]:
        period = campaign.settings.period
        parts: dict[str, Part] = {}
        left: dict[str, Left] = {}
        for region in campaign.settings.regions:
            current = self.versions.current(region, period)
            if current is None:
                left[region] = Left(region, model.MISSING, "no form was sent")
                continue
            if current.errors:
                plural = "error" if current.errors == 1 else "errors"
                left[region] = Left(
                    region,
                    model.INVALID,
                    f"version {current.number} has {current.errors} {plural}",
                )
                continue
            data = self.versions.content(region, period, current.number)
            try:
                rows = read_sheet(io.BytesIO(data), f"{region}/{period}.xlsx").rows
            except ActionError as error:  # a version is kept only if it could be read
                left[region] = Left(region, model.INVALID, error.message)
                continue
            figures = []
            for row in rows:
                state, number = read_value(row["value"])
                if state in (State.VALUE, State.ZERO) and number is not None:
                    figures.append((str(row["code"]), str(row["name"]), number))
            parts[region] = Part(region, current.number, current.sha256, tuple(figures))
        return parts, left

    # -- the operations --------------------------------------------------------------------------
    def make(self, campaign_id: str, kind: str, by: str) -> Stored:
        campaign = self.campaigns.get(campaign_id)
        if kind not in model.KINDS:
            raise ConsolidationError(
                "E-CONS-004", f"the kind must be one of {', '.join(model.KINDS)}"
            )
        if campaign.status not in _FROM:
            raise ConsolidationError(
                "E-CONS-003",
                f"a result is made while the campaign is {life.VALIDATING}, {life.READY} or"
                f" {life.PRELIMINARY}; it is {campaign.status}",
            )
        s = campaign.settings
        assert s.preliminary_min is not None and s.final_min is not None
        parts, left = self._gather(campaign)
        result = model.consolidate(kind, s.regions, parts, left, s.preliminary_min, s.final_min)
        now = self.clock()
        status = campaign.status
        for to in (*_FROM[status], _AFTER[kind]):
            life.move(status, to, life.SYSTEM)
            if not self.campaigns.set_status(campaign_id, status, to, now):
                raise CampaignError(
                    "E-CAMP-007", "the campaign changed while you were asking; read it again"
                )
            status = to
        # an earlier result is not the current one any more, although no form changed
        self.repo.mark_stale(campaign_id, now, "a newer result was made")
        return self.repo.add(campaign_id, result, by, now)

    def all(self, campaign_id: str) -> list[Stored]:
        self.campaigns.get(campaign_id)
        return self.repo.all(campaign_id)

    def get(self, campaign_id: str, number: int) -> Stored:
        self.campaigns.get(campaign_id)
        return self.repo.get(campaign_id, number)

    # -- a form of the period changed ------------------------------------------------------------
    def on_form(self, region: str, quarter: str, what: str) -> None:
        """A form was sent again or removed: the results that counted on the forms as they were
        are stale, and a campaign whose result was shown goes back to collecting."""
        now = self.clock()
        for campaign in self.campaigns.of_period(quarter):
            if region not in campaign.settings.regions or campaign.status == life.ARCHIVED:
                continue
            self.repo.mark_stale(campaign.id, now, f"the form of {region} {what}")
            if campaign.status in _SHOWN:
                life.move(campaign.status, life.COLLECTING, life.SYSTEM)
                self.campaigns.set_status(campaign.id, campaign.status, life.COLLECTING, now)
