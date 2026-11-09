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
    RUN_RESUMED = "RUN_RESUMED"  # the engine restarted and rebuilt the run from its history
    STEP_UNCERTAIN = "STEP_UNCERTAIN"  # a crash left a non-idempotent call whose result is unknown
    STEP_RESOLVED = "STEP_RESOLVED"  # a person decided about an uncertain step (`decision`)
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
    decision: str | None = None  # STEP_RESOLVED: "done" or "retry"
    # STEP_UNCERTAIN: `state` is where the step was (RUNNING, or COMPENSATING for an undo) and
    # `attempt` the attempt whose result is unknown; STEP_RESOLVED "retry": `attempt` is the new one


_STRING_FIELDS = ("step", "code", "message", "decision")
_FIELDS = {"seq", "kind", "at", "state", "outcome", "output", "attempt", "delay", *_STRING_FIELDS}


def event_to_dict(event: Event) -> dict[str, object]:
    """The event as plain data: names for the enums, nothing else changed."""
    return {
        "seq": event.seq,
        "kind": event.kind.value,
        "at": event.at,
        "step": event.step,
        "state": event.state.value if event.state is not None else None,
        "outcome": event.outcome.value if event.outcome is not None else None,
        "code": event.code,
        "message": event.message,
        "output": event.output,
        "attempt": event.attempt,
        "delay": event.delay,
        "decision": event.decision,
    }


def _number(data: dict[str, object], key: str, integer: bool) -> int | float | None:
    value = data.get(key)
    if value is None:
        return None
    wrong = isinstance(value, bool) or not isinstance(value, int | float)
    if wrong or (integer and not isinstance(value, int)):
        raise ValueError(f"event: {key} must be {'an integer' if integer else 'a number'}")
    assert isinstance(value, int | float)  # checked above; tells the type checker
    return value


def event_from_dict(data: dict[str, object]) -> Event:
    """Read an event from plain data; anything that is not an event is a ValueError."""
    extra = set(data) - _FIELDS
    if extra:
        raise ValueError(f"event: unknown field {sorted(extra)[0]}")
    seq = _number(data, "seq", integer=True)
    at = _number(data, "at", integer=False)
    if seq is None or at is None or seq < 0:
        raise ValueError("event: seq (from 0) and at are required")
    try:
        kind = EventKind(data.get("kind"))
        state = StepState(data["state"]) if data.get("state") is not None else None
        outcome = RunOutcome(data["outcome"]) if data.get("outcome") is not None else None
    except ValueError as error:
        raise ValueError(f"event: {error}") from None
    for key in _STRING_FIELDS:
        if data.get(key) is not None and not isinstance(data[key], str):
            raise ValueError(f"event: {key} must be text")
    output = data.get("output")
    if output is not None and not isinstance(output, dict):
        raise ValueError("event: output must be an object")
    attempt = _number(data, "attempt", integer=True)
    delay = _number(data, "delay", integer=False)
    return Event(
        int(seq),
        kind,
        float(at),
        step=data.get("step"),  # type: ignore[arg-type]
        state=state,
        outcome=outcome,
        code=data.get("code"),  # type: ignore[arg-type]
        message=data.get("message"),  # type: ignore[arg-type]
        output=output,
        attempt=int(attempt) if attempt is not None else None,
        delay=float(delay) if delay is not None else None,
        decision=data.get("decision"),  # type: ignore[arg-type]
    )
