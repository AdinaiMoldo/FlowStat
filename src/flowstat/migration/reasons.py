"""Why a run cannot move to another version (docs/api-spec.md section 10)."""

from __future__ import annotations

from dataclasses import dataclass

NEVER_MIGRATE = "E-MIG-001"
RUNNING_STEP = "E-MIG-002"
REMOVED_TOUCHED_STEP = "E-MIG-003"
CHANGED_TOUCHED_STEP = "E-MIG-004"
UPSTREAM_CHANGED = "E-MIG-005"
GUARD_CONTRADICTS_HISTORY = "E-MIG-006"
OUTPUT_INCOMPATIBLE = "E-MIG-007"
INPUTS_REJECTED = "E-MIG-008"


@dataclass(frozen=True, slots=True)
class Reason:
    code: str
    step: str | None  # the step the reason is about (an input name for E-MIG-008)
    message: str
