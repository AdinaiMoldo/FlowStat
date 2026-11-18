"""A reporting campaign: one period of collecting the regional reports (US-53).

The life cycle is a table of the changes that are allowed, and for each one who makes it. A change
that a person makes (``MANUAL``) can be asked for through the API; a change that follows from
something the system does, such as a consolidation that was made or an approval that was given
(``SYSTEM``), cannot, because a person who could write ``APPROVED`` into a campaign would be
approving nothing. The system's changes are listed here so that the table is the whole life cycle;
the stories that make them (consolidation, approval) call ``move`` with ``SYSTEM``.

Pure: no clock, no storage. The module uses the standard library only.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date

from flowstat.model import rules as content_rules

DRAFT = "DRAFT"
OPEN = "OPEN"
COLLECTING = "COLLECTING"
VALIDATING = "VALIDATING"
READY = "READY_FOR_CONSOLIDATION"
PRELIMINARY = "PRELIMINARY"
PENDING = "PENDING_APPROVAL"
APPROVED = "APPROVED"
REJECTED = "REJECTED"
FINALIZED = "FINALIZED"
ARCHIVED = "ARCHIVED"

STATUSES = (
    DRAFT, OPEN, COLLECTING, VALIDATING, READY, PRELIMINARY, PENDING,
    APPROVED, REJECTED, FINALIZED, ARCHIVED,
)  # fmt: skip

MANUAL = "manual"
SYSTEM = "system"

# from -> to -> who makes the change
EDGES: dict[str, dict[str, str]] = {
    DRAFT: {OPEN: MANUAL, ARCHIVED: MANUAL},  # ARCHIVED here is a campaign that was cancelled
    OPEN: {COLLECTING: MANUAL},
    COLLECTING: {VALIDATING: MANUAL},
    VALIDATING: {COLLECTING: MANUAL, READY: SYSTEM},
    READY: {PRELIMINARY: SYSTEM, PENDING: SYSTEM, COLLECTING: SYSTEM},
    PRELIMINARY: {COLLECTING: SYSTEM, READY: SYSTEM},
    PENDING: {APPROVED: SYSTEM, REJECTED: SYSTEM, COLLECTING: SYSTEM},
    APPROVED: {FINALIZED: MANUAL},
    REJECTED: {COLLECTING: MANUAL, ARCHIVED: MANUAL},
    FINALIZED: {ARCHIVED: MANUAL},
    ARCHIVED: {},
}

# The forms of a period cannot be replaced or removed once its result is approved: that would
# change what was approved without anyone approving it again.
LOCKED = frozenset({APPROVED, FINALIZED, ARCHIVED})

NAME_MAX = 80
SAFE_CODE = re.compile(r"^[A-Za-z0-9_-]{1,16}$")
APPROVER = "chief_statistician"


class CampaignError(ValueError):
    """A campaign that cannot be made or a change that is not allowed; ``code`` says which."""

    def __init__(self, code: str, message: str, field_name: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.field = field_name


@dataclass(frozen=True, slots=True)
class Settings:
    """What a person writes when creating or editing a campaign."""

    name: str
    year: int
    quarter: int
    regions: tuple[str, ...]
    deadline: str  # a date, YYYY-MM-DD
    workflow: str
    workflow_version: int
    template: str = "regional-indicators-v1"  # the type or version of the form
    rules: str = "basic"  # the set of validation rules (US-54)
    preliminary_min: int | None = None  # valid forms from which a preliminary result is made
    final_min: int | None = None  # valid forms that the final result needs
    approver: str = APPROVER  # the title that approves

    @property
    def period(self) -> str:
        return f"{self.year}Q{self.quarter}"


def default_thresholds(regions: int) -> tuple[int, int]:
    """Preliminary from about three quarters of the regions, final from all of them."""
    return min(regions, max(1, math.ceil(regions * 0.75))), regions


def checked(settings: Settings, known_regions: set[str]) -> Settings:
    """The settings with the thresholds filled in, or a ``CampaignError`` naming the field."""
    name = settings.name.strip()
    if not name or len(name) > NAME_MAX:
        raise CampaignError("E-CAMP-001", f"the name must have 1 to {NAME_MAX} characters", "name")
    if not 2000 <= settings.year <= 2100:
        raise CampaignError("E-CAMP-001", "the year must be between 2000 and 2100", "year")
    if settings.quarter not in (1, 2, 3, 4):
        raise CampaignError("E-CAMP-001", "the quarter must be 1, 2, 3 or 4", "quarter")
    regions = settings.regions
    if not regions:
        raise CampaignError("E-CAMP-001", "choose at least one region", "regions")
    if len(set(regions)) != len(regions):
        raise CampaignError("E-CAMP-001", "a region is listed twice", "regions")
    for code in regions:
        if not SAFE_CODE.match(code) or code not in known_regions:
            raise CampaignError(
                "E-CAMP-001", f"the region {code} is not in the catalogue", "regions"
            )
    try:
        date.fromisoformat(settings.deadline)
    except ValueError:
        raise CampaignError(
            "E-CAMP-001", "the deadline must be a date like 2026-11-30", "deadline"
        ) from None
    if not settings.workflow.strip() or settings.workflow_version < 1:
        raise CampaignError("E-CAMP-001", "name a workflow and its version", "workflow")
    if not content_rules.known(settings.rules.strip()):
        known = ", ".join(sorted(content_rules.SETS))
        raise CampaignError("E-CAMP-001", f"the rules must be one of: {known}", "rules")
    if not settings.approver.strip():
        raise CampaignError("E-CAMP-001", "name the title that approves", "approver")
    low, high = default_thresholds(len(regions))
    preliminary = low if settings.preliminary_min is None else settings.preliminary_min
    final = high if settings.final_min is None else settings.final_min
    if not 1 <= preliminary <= final <= len(regions):
        raise CampaignError(
            "E-CAMP-001",
            f"the thresholds must satisfy 1 <= preliminary <= final <= {len(regions)}",
            "final_min",
        )
    return Settings(
        name, settings.year, settings.quarter, regions, settings.deadline,
        settings.workflow.strip(), settings.workflow_version, settings.template.strip(),
        settings.rules.strip(), preliminary, final, settings.approver.strip(),
    )  # fmt: skip


def move(current: str, to: str, by: str) -> str:
    """The new status, or a ``CampaignError``: ``by`` is MANUAL for a person and SYSTEM for the
    system. A change that is in the table but belongs to the other party is refused with its own
    code, so the page can say that the system makes it."""
    if current not in EDGES or to not in STATUSES:
        raise CampaignError("E-CAMP-002", f"there is no status {to!r}", "to")
    who = EDGES[current].get(to)
    if who is None:
        raise CampaignError("E-CAMP-002", f"a campaign cannot go from {current} to {to}", "to")
    if who != by:
        raise CampaignError(
            "E-CAMP-003",
            f"the change from {current} to {to} is made by the system, not by a person",
            "to",
        )
    return to


def manual_moves(current: str) -> list[str]:
    """The changes a person may ask for now."""
    return [to for to, who in EDGES.get(current, {}).items() if who == MANUAL]


@dataclass(frozen=True, slots=True)
class Campaign:
    id: str
    settings: Settings
    status: str
    created_by: str
    created_at: str
    updated_at: str
