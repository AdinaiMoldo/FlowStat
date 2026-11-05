"""Events of a run: the history that A2 stores and C4 replays."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from flowstat.model.states import RunOutcome, StepState


class EventKind(Enum):
    RUN_STARTED = "RUN_STARTED"
    STEP_STARTED = "STEP_STARTED"
    STEP_WAITING = "STEP_WAITING"
    STEP_FINISHED = "STEP_FINISHED"
    RUN_FINISHED = "RUN_FINISHED"


@dataclass(frozen=True, slots=True)
class Event:
    seq: int  # 0, 1, 2, ... within one run
    kind: EventKind
    at: float  # seconds; given by the caller, never read from a clock here
    step: str | None = None
    state: StepState | None = None  # STEP_FINISHED: the final state of the step
    outcome: RunOutcome | None = None  # RUN_FINISHED
    code: str | None = None  # a failed step: the error code and message
    message: str | None = None
    output: dict[str, object] | None = None  # a succeeded step: what it produced
