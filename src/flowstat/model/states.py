"""States of a step and the outcome of a run (docs/dsl-spec.md section 5, architecture.md)."""

from __future__ import annotations

from enum import Enum


class StepState(Enum):
    PENDING = "PENDING"  # waiting for its predecessors
    READY = "READY"  # may run, waiting for a free slot
    RUNNING = "RUNNING"
    WAITING = "WAITING"  # paused for an external answer; the slot is free
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"  # its condition was false, or its join cannot be satisfied
    UPSTREAM_FAILED = "UPSTREAM_FAILED"  # a predecessor failed and the join cannot be satisfied
    CANCELLED = "CANCELLED"  # the run was stopped before the step finished
    COMPENSATING = "COMPENSATING"  # the undo of a succeeded step is running (dsl-spec 5.4)
    COMPENSATED = "COMPENSATED"  # the step succeeded and was undone
    COMPENSATION_FAILED = "COMPENSATION_FAILED"  # the undo failed; the step is not undone


TERMINAL_STEP_STATES = frozenset(
    {
        StepState.SUCCEEDED,
        StepState.FAILED,
        StepState.SKIPPED,
        StepState.UPSTREAM_FAILED,
        StepState.CANCELLED,
        StepState.COMPENSATED,
        StepState.COMPENSATION_FAILED,
    }
)


class RunOutcome(Enum):
    SUCCEEDED = "SUCCEEDED"  # the goals hold
    GOAL_NOT_REACHED = "GOAL_NOT_REACHED"  # the run ended and the goals do not hold (DR-02)
    FAILED = "FAILED"  # a step with the default failure policy failed
    CANCELLED = "CANCELLED"  # stopped on request
    COMPENSATED = "COMPENSATED"  # a step failed under on_failure: compensate; all work was undone
    COMPENSATION_FAILED = "COMPENSATION_FAILED"  # some work could not be undone
