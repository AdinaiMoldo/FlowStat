"""Events of a run: the history that A2 stores and C4 replays."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from flowstat.model.states import RunOutcome, StepState


class EventKind(Enum):
    RUN_STARTED = "RUN_STARTED"
    STEP_STARTED = "STEP_STARTED"
    STEP_WAITING = "STEP_WAITING"
    STEP_RETRY = "STEP_RETRY"  # a transient failure; the step gets another attempt after a delay
    STEP_FINISHED = "STEP_FINISHED"
    COMPENSATION_STARTED = "COMPENSATION_STARTED"  # the run stopped and now undoes its work
    STEP_COMPENSATING = "STEP_COMPENSATING"  # the undo of a step starts (attempt in `attempt`)
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
    attempt: int | None = None  # STEP_RETRY: the number of the attempt that starts next
    delay: float | None = None  # STEP_RETRY: seconds to wait before that attempt
