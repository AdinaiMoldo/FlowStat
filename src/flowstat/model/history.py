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
    run_started: float | None = None
    run_finished: float | None = None
    outcome: RunOutcome | None = None
    for event in events:
        step = event.step
        if event.kind is EventKind.RUN_STARTED:
            run_started = event.at
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
        elif event.kind is EventKind.STEP_WAITING:
            state[step] = StepState.WAITING
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
            record[s].code if s in record else None,
            record[s].message if s in record else None,
            record[s].output if s in record else None,
            attempts.get(s, 0),
        )
        for s in step_ids
    ]
    return RunReport(
        run_id,
        workflow,
        version,
        _status(run_started, outcome, state),
        outcome,
        run_started,
        run_finished,
        steps,
    )


def _status(
    run_started: float | None, outcome: RunOutcome | None, state: dict[str, StepState]
) -> str:
    if outcome is not None:
        return "FINISHED"
    if run_started is None:
        return "CREATED"
    values = list(state.values())
    if StepState.WAITING in values and StepState.RUNNING not in values:
        return "WAITING"
    return "RUNNING"
