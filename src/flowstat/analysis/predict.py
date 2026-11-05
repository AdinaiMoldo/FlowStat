"""Prediction of a run (C6): which steps run and which are skipped for given values.

It is the optimistic run of a workflow, written for concrete values: every action succeeds, a
guard is true or false by the evaluation of its predicate, and the join rules of dsl-spec 5 decide
the rest. The generator of test inputs uses it to know what a case covers, and the mocked run
compares what the engine did with it. A guard outside the fragment, or one that reads a step that
did not run, gives ``unknown``; so does everything that depends on it and could go either way.
"""

from __future__ import annotations

from collections.abc import Mapping

from flowstat.compiler.plan import JoinSpec, Plan
from flowstat.solver.predicate import Pred, holds, variables

RUN = "run"
SKIP = "skip"
UNKNOWN = "unknown"


def predict(plan: Plan, preds: Mapping[str, Pred], values: Mapping[str, object]) -> dict[str, str]:
    """``run``, ``skip`` or ``unknown`` for every step, given the value of each variable."""
    states: dict[str, str] = {}
    for step in plan.steps:
        gate = _join(step.join, [states[d] for d in step.needs])
        if gate != RUN:
            states[step.id] = SKIP if gate == SKIP else UNKNOWN
            continue
        pred = preds.get(step.id)
        if pred is None:
            states[step.id] = RUN
            continue
        if any(_owner(name) not in ("inputs", *_ran(states)) for name in variables(pred)):
            states[step.id] = UNKNOWN  # it reads the output of a step that did not run
            continue
        truth = holds(pred, values)
        states[step.id] = UNKNOWN if truth is None else RUN if truth else SKIP
    return states


def _owner(variable: str) -> str:
    return variable.rpartition(".")[0]


def _ran(states: Mapping[str, str]) -> list[str]:
    return [s for s, state in states.items() if state == RUN]


def _join(join: JoinSpec, deps: list[str]) -> str:
    """Whether the join rule is met by the dependencies: ``run`` (met), ``skip`` or ``unknown``."""
    if not deps:
        return RUN
    ran, unknown = deps.count(RUN), deps.count(UNKNOWN)
    if join.kind == "all_done":
        return RUN
    if join.kind == "all_success":
        need = len(deps)
    elif join.kind == "any_success":
        need = 1
    else:
        need = join.k
    if ran >= need:
        return RUN
    return UNKNOWN if ran + unknown >= need else SKIP
