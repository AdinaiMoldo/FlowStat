"""The report of a run: a fold over its events (US-11).

The stored history is the events; everything a person sees about a run (status of each step, start,
end, error message) is computed from them, so there is no second copy that could disagree.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from flowstat.model.events import Event, EventKind
from flowstat.model.states import RunOutcome, StepState


@dataclass(frozen=True, slots=True)
class StepRecord:
    step: str
    state: StepState
    started_at: float | None = None  # None: the step never ran (skipped, upstream failed, ...)
    finished_at: float | None = None
    code: str | None = None
    message: str | None = None
    output: dict[str, object] | None = None
    attempts: int = 0  # 0: never ran; 1: ran once; more: retried after transient failures
    compensated_at: float | None = None  # when the undo of the step finished


@dataclass(frozen=True, slots=True)
class RunReport:
    run_id: str
    workflow: str
    version: int
    status: str  # CREATED, RUNNING, WAITING or FINISHED
    outcome: RunOutcome | None
    started_at: float | None
    finished_at: float | None
    steps: list[StepRecord]


def report_from(
    run_id: str, workflow: str, version: int, events: Iterable[Event], step_ids: list[str]
) -> RunReport:
    """Fold the events into a report. Steps appear in the order of the plan (``step_ids``)."""
    state = {s: StepState.PENDING for s in step_ids}
    started: dict[str, float] = {}
    attempts: dict[str, int] = {}
    finished: dict[str, float] = {}
    record: dict[str, Event] = {}
    undo: dict[str, Event] = {}  # the final event of an undo (COMPENSATED or its failure)
    unsure: dict[str, Event] = {}  # the STEP_UNCERTAIN of a step that is still uncertain
    compensating = False
    run_started: float | None = None
    run_finished: float | None = None
    outcome: RunOutcome | None = None
    for event in events:
        step = event.step
        if event.kind is EventKind.RUN_STARTED:
            run_started = event.at
        elif event.kind is EventKind.COMPENSATION_STARTED:
            compensating = True
        elif event.kind is EventKind.RUN_FINISHED:
            run_finished, outcome = event.at, event.outcome
        elif step is None or step not in state:
            continue
        elif event.kind is EventKind.STEP_STARTED:
            state[step] = StepState.RUNNING
            started[step] = event.at
            attempts[step] = max(attempts.get(step, 0), 1)
        elif event.kind is EventKind.STEP_RETRY and event.attempt is not None:
            attempts[step] = max(attempts.get(step, 0), event.attempt)
        elif event.kind is EventKind.STEP_COMPENSATING:
            state[step] = StepState.COMPENSATING
        elif event.kind is EventKind.STEP_WAITING:
            state[step] = StepState.WAITING
        elif event.kind is EventKind.STEP_UNCERTAIN:
            state[step] = StepState.UNCERTAIN
            unsure[step] = event
        elif event.kind is EventKind.STEP_RESOLVED:
            was = unsure.pop(step, None)
            if event.decision == "retry":
                undoing = was is not None and was.state is StepState.COMPENSATING
                state[step] = StepState.COMPENSATING if undoing else StepState.RUNNING
                if event.attempt is not None:
                    attempts[step] = max(attempts.get(step, 0), event.attempt)
        elif event.kind is EventKind.STEP_FINISHED and event.state in _UNDO_STATES:
            state[step] = event.state
            undo[step] = event
        elif event.kind is EventKind.STEP_FINISHED and event.state is not None:
            state[step] = event.state
            finished[step] = event.at
            record[step] = event
    steps = [
        StepRecord(
            s,
            state[s],
            started.get(s),
            finished.get(s),
            _error(s, record, undo, unsure)[0],
            _error(s, record, undo, unsure)[1],
            record[s].output if s in record else None,
            attempts.get(s, 0),
            undo[s].at if s in undo else None,
        )
        for s in step_ids
    ]
    return RunReport(
        run_id,
        workflow,
        version,
        _status(run_started, outcome, state, compensating),
        outcome,
        run_started,
        run_finished,
        steps,
    )


_UNDO_STATES = frozenset({StepState.COMPENSATED, StepState.COMPENSATION_FAILED})


def _error(
    step: str, record: dict[str, Event], undo: dict[str, Event], unsure: dict[str, Event]
) -> tuple[str | None, str | None]:
    """The error to show: why the step is uncertain, that of a failed undo, or that of the step."""
    if step in unsure:
        return unsure[step].code, unsure[step].message
    if step in undo and undo[step].code is not None:
        return undo[step].code, undo[step].message
    if step in record:
        return record[step].code, record[step].message
    return None, None


def _status(
    run_started: float | None,
    outcome: RunOutcome | None,
    state: dict[str, StepState],
    compensating: bool,
) -> str:
    if outcome is not None:
        return "FINISHED"
    if run_started is None:
        return "CREATED"
    values = list(state.values())
    active = StepState.RUNNING in values or StepState.COMPENSATING in values
    if StepState.UNCERTAIN in values and not active:
        return "WAITING"  # only a person can move the run on
    if compensating:
        return "COMPENSATING"
    if StepState.WAITING in values and StepState.RUNNING not in values:
        return "WAITING"
    return "RUNNING"
