"""Can the history of a run be explained by the new version of its workflow? (C5, level 1)

The recorded history is replayed over the plan of the new version with the decision function of
the engine: for every step that has events, the new plan must have made the same decision from the
same recorded values (its join rule, its guard), the step must be the same piece of work (action,
arguments, checks, undo), and what the steps that have not started will read must exist in the
recorded outputs. Nothing is executed and nothing is stored; the answer is a verdict with every
reason found, so that a person can fix all of them in one go (docs/algorithms/C5.md).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from flowstat.analysis.signatures import Signatures
from flowstat.compiler.catalogue import Catalogue
from flowstat.compiler.inputs import validate_inputs
from flowstat.compiler.plan import Plan, PlanStep
from flowstat.dsl.ast import Expr, Ref, to_source
from flowstat.engine.evaluator import EvalContext, EvalError, evaluate_bool
from flowstat.engine.scheduler import join_verdict
from flowstat.migration import reasons as R
from flowstat.migration.reasons import Reason
from flowstat.migration.retype import incompatibilities
from flowstat.model.events import Event, EventKind
from flowstat.model.states import TERMINAL_STEP_STATES, StepState

_UNDO = frozenset({StepState.COMPENSATING, StepState.COMPENSATED, StepState.COMPENSATION_FAILED})


@dataclass(frozen=True, slots=True)
class Verdict:
    migratable: bool
    reasons: tuple[Reason, ...]
    kept: tuple[str, ...]  # steps with history that the new version has
    added: tuple[str, ...]  # steps of the new version that the old one did not have
    pending: tuple[str, ...]  # steps of the new version without history: they run after the move
    dropped: tuple[str, ...]  # steps of the old version the new one lacks (never touched)
    inputs: Mapping[str, object] = field(default_factory=dict)  # the inputs for the new version


@dataclass(slots=True)
class _History:
    states: dict[str, StepState] = field(default_factory=dict)
    started: set[str] = field(default_factory=set)  # steps with a STEP_STARTED event
    touched: set[str] = field(default_factory=set)  # steps with any event
    outputs: dict[str, dict[str, object]] = field(default_factory=dict)
    finished: bool = False
    undoing: bool = False


def _fold(events: Sequence[Event]) -> _History:
    """The state of every step after the events (the same reading as ``Scheduler.restore``)."""
    h = _History()
    undo_uncertain: set[str] = set()
    for e in events:
        if e.kind is EventKind.RUN_FINISHED:
            h.finished = True
        elif e.kind is EventKind.COMPENSATION_STARTED:
            h.undoing = True
        if e.step is None:
            continue
        step = e.step
        h.touched.add(step)
        if e.kind is EventKind.STEP_STARTED:
            h.states[step] = StepState.RUNNING
            h.started.add(step)
        elif e.kind is EventKind.STEP_WAITING:
            h.states[step] = StepState.WAITING
        elif e.kind is EventKind.STEP_COMPENSATING:
            h.states[step] = StepState.COMPENSATING
        elif e.kind is EventKind.STEP_UNCERTAIN:
            h.states[step] = StepState.UNCERTAIN
            if e.state is StepState.COMPENSATING:
                undo_uncertain.add(step)
        elif e.kind is EventKind.STEP_RESOLVED:
            if e.decision == "retry":
                undo = step in undo_uncertain
                h.states[step] = StepState.COMPENSATING if undo else StepState.RUNNING
            undo_uncertain.discard(step)
        elif e.kind is EventKind.STEP_FINISHED and e.state is not None:
            h.states[step] = e.state
            if e.state is StepState.SUCCEEDED:
                h.outputs[step] = dict(e.output or {})
    return h


def resolve_inputs(
    new: Plan, inputs: Mapping[str, object], catalogue: Catalogue
) -> tuple[dict[str, object], list[Reason]]:
    """The recorded inputs as the new version reads them: its defaults added, an input it no
    longer declares left out, and a reason for each value it refuses."""
    resolved, errors = validate_inputs(new, dict(inputs), catalogue)
    reasons = [
        Reason(
            R.INPUTS_REJECTED, e.input, f"the new version refuses input '{e.input}': {e.message}"
        )
        for e in errors
        if e.code != "E-INPUT-005"  # an input that was removed is simply not passed on
    ]
    return resolved, reasons


def check(
    old: Plan,
    new: Plan,
    events: Sequence[Event],
    inputs: Mapping[str, object],
    signatures: Signatures,
    catalogue: Catalogue,
) -> Verdict:
    """Whether the run whose history is ``events`` (of ``old``) can go on under ``new``."""
    h = _fold(events)
    old_ids = {s.id for s in old.steps}
    new_by_id = {s.id: s for s in new.steps}
    old_by_id = {s.id: s for s in old.steps}
    kept = tuple(s.id for s in new.steps if s.id in h.touched)
    added = tuple(s.id for s in new.steps if s.id not in old_ids)
    pending = tuple(s.id for s in new.steps if s.id not in h.touched)
    dropped = tuple(s.id for s in old.steps if s.id not in new_by_id and s.id not in h.touched)

    def verdict(found: list[Reason], resolved: Mapping[str, object]) -> Verdict:
        return Verdict(not found, tuple(found), kept, added, pending, dropped, resolved)

    never = _never(h)
    if never:
        return verdict(never, {})
    resolved, found = resolve_inputs(new, inputs, catalogue)
    for step_id in sorted(h.touched):
        if h.states.get(step_id) is StepState.RUNNING:
            found.append(
                Reason(R.RUNNING_STEP, step_id, f"{step_id} is running; ask again when it ends")
            )
    ctx = EvalContext(
        resolved,
        h.outputs,
        {s.base: new.instances_of(s.base) for s in new.steps if s.loop is not None},
    )
    for step_id in (s.id for s in old.steps if s.id in h.touched):
        if step_id not in new_by_id:
            found.append(
                Reason(
                    R.REMOVED_TOUCHED_STEP,
                    step_id,
                    f"{step_id} has run and the new version does not have it "
                    "(a rename is a removal and a new step)",
                )
            )
            continue
        what = _changed(old_by_id[step_id], new_by_id[step_id], h.states.get(step_id))
        if what:
            text = f"{step_id} has run and its {', '.join(what)} changed"
            found.append(Reason(R.CHANGED_TOUCHED_STEP, step_id, text))
    for step in new.steps:
        if step.id in h.touched:
            found.extend(_decision(step, new, h, ctx))
    future = [s for s in new.steps if s.id not in h.touched]
    found.extend(incompatibilities(future, ctx, signatures))
    return verdict(found, resolved)


def _never(h: _History) -> list[Reason]:
    """A run that has ended, is undoing its work or has an uncertain step is not moved."""
    found: list[Reason] = []
    if h.finished:
        found.append(Reason(R.NEVER_MIGRATE, None, "the run has finished"))
    if h.undoing or any(s in _UNDO for s in h.states.values()):
        found.append(Reason(R.NEVER_MIGRATE, None, "the run is undoing its work (compensation)"))
    found.extend(
        Reason(R.NEVER_MIGRATE, step, f"{step} is uncertain: a person must decide first")
        for step, state in sorted(h.states.items())
        if state is StepState.UNCERTAIN
    )
    return found


def _canonical(step: PlanStep) -> dict[str, object]:
    """What makes a step the same piece of work; positions in the text do not count."""

    def text(expr: Expr | None) -> str | None:
        return None if expr is None else to_source(expr)

    def part(p: object) -> object:
        return to_source(p) if isinstance(p, Ref) else repr(p)

    return {
        "action": step.action,
        "arguments": tuple((n, tuple(part(p) for p in parts)) for n, parts in step.with_),
        "ensures": text(step.ensures),
        "compensate": step.compensate,
        "loop": step.loop,
    }


def _changed(old: PlanStep, new: PlanStep, state: StepState | None) -> list[str]:
    a, b = _canonical(old), _canonical(new)
    names = {"arguments": "arguments (with)", "ensures": "ensures", "compensate": "compensate"}
    what = [names.get(k, k) for k in a if a[k] != b[k]]
    # the failure policy of a step that failed has already been applied to the rest of the run
    if state is StepState.FAILED and old.on_failure != new.on_failure:
        what.append("on_failure")
    return what


def _guard(step: PlanStep, ctx: EvalContext) -> tuple[bool | None, str]:
    """The new guard on the recorded values: (value, ""), or (None, why it cannot be read)."""
    if step.when is None:
        return True, ""
    try:
        return evaluate_bool(step.when, ctx), ""
    except EvalError as error:
        return None, error.message


def _decision(step: PlanStep, new: Plan, h: _History, ctx: EvalContext) -> list[Reason]:
    """The recorded fate of a step must be what the new plan decides from the recorded values."""
    state = h.states.get(step.id)
    if state is None:
        return []
    preds = [h.states.get(p, StepState.PENDING) for p in step.needs]
    open_ = [p for p, s in zip(step.needs, preds, strict=True) if s not in TERMINAL_STEP_STATES]
    if open_ and state is not StepState.CANCELLED:
        text = f"{step.id} began before {', '.join(open_)} ended, which it needs in the new version"
        return [Reason(R.UPSTREAM_CHANGED, step.id, text)]
    if state is StepState.CANCELLED:
        return []
    join = join_verdict(step.join, preds)
    if state in (StepState.SKIPPED, StepState.UPSTREAM_FAILED) and join is not None:
        if join is state:
            return []
        text = f"{step.id} ended as {state.value}; the new join rule gives {join.value}"
        return [Reason(R.UPSTREAM_CHANGED, step.id, text)]
    if join is not None:
        text = f"{step.id} ran, and the new join rule would have given {join.value}"
        return [Reason(R.UPSTREAM_CHANGED, step.id, text)]
    if state is StepState.UPSTREAM_FAILED:
        text = f"{step.id} ended as UPSTREAM_FAILED; the new join rule lets it run"
        return [Reason(R.UPSTREAM_CHANGED, step.id, text)]
    holds, why = _guard(step, ctx)
    if step.id in h.started:  # it ran: the new guard must have let it
        if holds:
            return []
        reason = why or "is false on the recorded values"
        return [Reason(R.GUARD_CONTRADICTS_HISTORY, step.id, f"{step.id} ran; its guard {reason}")]
    if state is StepState.SKIPPED:  # it was skipped: the new guard must skip it too
        if step.when is None:
            text = f"{step.id} was skipped and the new version has no guard or join rule for it"
            return [Reason(R.UPSTREAM_CHANGED, step.id, text)]
        if holds is False:
            return []
        reason = why or "is true on the recorded values"
        text = f"{step.id} was skipped; its new guard {reason}"
        return [Reason(R.GUARD_CONTRADICTS_HISTORY, step.id, text)]
    if (
        state is StepState.FAILED and step.when is not None and holds is not None
    ):  # failed in its guard, now it is readable
        text = f"{step.id} failed in its guard; the new guard can be read"
        return [Reason(R.GUARD_CONTRADICTS_HISTORY, step.id, text)]
    return []
