"""A stored version of a workflow (A1)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class VersionStatus(Enum):
    DRAFT = "draft"
    PUBLISHED = "published"


@dataclass(frozen=True, slots=True)
class Version:
    name: str
    number: int
    status: VersionStatus
    text: str  # the workflow document as the designer wrote it
    catalogue: str  # JSON snapshot of the catalogue lists the version was compiled against
    created_at: str  # ISO 8601, passed in by the caller (deterministic mode)
    published_at: str | None
