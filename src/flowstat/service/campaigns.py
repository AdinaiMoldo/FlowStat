"""Reporting campaigns (US-53): create, edit while a draft, move through the life cycle, and say
how far the collecting has come.

The life cycle itself is ``model/campaign.py``. This service adds what needs the other parts: the
regions must be in the catalogue, the workflow version must exist (and be published before the
campaign opens), and the progress is read from the forms that the regions have sent.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from flowstat.model import campaign as life
from flowstat.model import rules as content
from flowstat.model.campaign import Campaign, CampaignError, Settings
from flowstat.model.diagnostics import Severity
from flowstat.model.versions import VersionStatus
from flowstat.repo.campaigns import CampaignNameTakenError, CampaignRepo
from flowstat.repo.versions import VersionNotFoundError, VersionStore
from flowstat.service.forms import FormService


@dataclass(frozen=True, slots=True)
class RegionProgress:
    region: str
    name: str
    received: bool
    valid: bool
    problems: int  # the errors that block the form
    warnings: int = 0  # told to the sender, they block nothing


@dataclass(frozen=True, slots=True)
class Progress:
    expected: int
    received: int
    valid: int
    invalid: int
    missing: tuple[str, ...]
    regions: tuple[RegionProgress, ...]
    preliminary_ready: bool  # enough valid forms for a preliminary result
    final_ready: bool  # enough valid forms for the final result


class CampaignService:
    def __init__(
        self,
        db: sqlite3.Connection,
        forms: FormService,
        new_id: Callable[[], str],
        clock: Callable[[], str],
    ) -> None:
        self.repo = CampaignRepo(db)
        self.versions = VersionStore(db)
        self.forms = forms
        self.new_id = new_id
        self.clock = clock

    # -- checks that need the catalogue and the versions ----------------------------------------
    def _checked(self, settings: Settings) -> Settings:
        done = life.checked(settings, set(self.forms.regions()))
        try:
            self.versions.get(done.workflow, done.workflow_version)
        except VersionNotFoundError:
            raise CampaignError(
                "E-CAMP-001",
                f"the workflow {done.workflow} has no version {done.workflow_version}",
                "workflow",
            ) from None
        return done

    # -- the operations ---------------------------------------------------------------------------
    def create(self, settings: Settings, by: str) -> Campaign:
        done = self._checked(settings)
        try:
            return self.repo.add(self.new_id(), done, life.DRAFT, by, self.clock())
        except CampaignNameTakenError:
            raise CampaignError(
                "E-CAMP-005", f"a campaign named {done.name!r} exists", "name"
            ) from None

    def update(self, campaign_id: str, settings: Settings) -> Campaign:
        current = self.repo.get(campaign_id)
        if current.status != life.DRAFT:
            raise CampaignError(
                "E-CAMP-004", f"a campaign can be edited only while it is {life.DRAFT}"
            )
        done = self._checked(settings)
        try:
            return self.repo.replace(campaign_id, done, self.clock())
        except CampaignNameTakenError:
            raise CampaignError(
                "E-CAMP-005", f"a campaign named {done.name!r} exists", "name"
            ) from None

    def move(self, campaign_id: str, to: str, by: str = life.MANUAL) -> Campaign:
        """Change the status. ``by`` is ``MANUAL`` for a person; the stories that make
        consolidations and approvals pass ``SYSTEM``."""
        current = self.repo.get(campaign_id)
        life.move(current.status, to, by)
        if to == life.OPEN:
            self._open_guard(current)
        if not self.repo.set_status(campaign_id, current.status, to, self.clock()):
            raise CampaignError(
                "E-CAMP-007", "the campaign changed while you were asking; read it again"
            )
        return self.repo.get(campaign_id)

    def _open_guard(self, campaign: Campaign) -> None:
        s = campaign.settings
        try:
            version = self.versions.get(s.workflow, s.workflow_version)
        except VersionNotFoundError:
            raise CampaignError(
                "E-CAMP-008", f"the workflow {s.workflow} v{s.workflow_version} does not exist"
            ) from None
        if version.status is not VersionStatus.PUBLISHED:
            raise CampaignError(
                "E-CAMP-008",
                f"publish {s.workflow} v{s.workflow_version} before the campaign is opened",
            )

    def get(self, campaign_id: str) -> Campaign:
        return self.repo.get(campaign_id)

    def all(self) -> list[Campaign]:
        return self.repo.all()

    # -- how far the collecting has come ---------------------------------------------------------
    def progress(self, campaign: Campaign) -> Progress:
        s = campaign.settings
        listed = {f.region: f for f in self.forms.listing(s.period, s.rules)}
        rows: list[RegionProgress] = []
        for code in s.regions:
            info = listed.get(code)
            if info is None or not info.received:
                rows.append(RegionProgress(code, info.name if info else code, False, False, 0))
            else:
                errors = len(content.blocking(info.findings)) or len(info.problems)
                warnings = sum(1 for f in info.findings if f.severity is Severity.WARNING)
                rows.append(
                    RegionProgress(code, info.name, True, not info.problems, errors, warnings)
                )
        valid = sum(1 for r in rows if r.valid)
        received = sum(1 for r in rows if r.received)
        assert s.preliminary_min is not None and s.final_min is not None
        return Progress(
            expected=len(rows),
            received=received,
            valid=valid,
            invalid=received - valid,
            missing=tuple(r.region for r in rows if not r.received),
            regions=tuple(rows),
            preliminary_ready=valid >= s.preliminary_min,
            final_ready=valid >= s.final_min,
        )
