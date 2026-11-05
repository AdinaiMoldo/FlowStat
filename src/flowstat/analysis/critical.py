"""The feasible critical path: priority of a ready step by the longest chain that can run (C3).

dsl-spec 11. A chain is a sequence of steps, each an ancestor of the next. It is feasible when the
conditions under which its steps run (``runs.py``) can hold together; the solver decides that. The
priority of a step is its duration plus the longest feasible chain behind it, so a long chain that
sits behind a guard that cannot be true in the same run as the rest does not count.

Every step keeps up to ``CAP`` alternatives: (length, condition) of the chains that start at it,
the longest first. A step meets the alternatives of all its descendants, which is how a chain
passes over a step that is skipped. When more than ``CAP`` chains are left at a step the rest are
dropped, except the longest, which is kept under a weaker condition, and the step is reported in
``cut``. So every priority is an upper bound of the length of the chain that really runs, and
the plain longest path is an upper bound of the priority; a cut only moves it towards the plain
path.

When the inputs of the run are known their values are put into the guards, so the priority is the
one of this run. An atom the solver cannot read counts as possible, which keeps an upper bound.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from flowstat.analysis.runs import bounds, conj, run_conditions, size
from flowstat.analysis.signatures import Signatures
from flowstat.analysis.variables import lower_guards
from flowstat.compiler.plan import Plan
from flowstat.solver.domains import Domain
from flowstat.solver.predicate import (
    FALSE,
    TRUE,
    And,
    Const,
    Not,
    Or,
    Pred,
    holds,
    variables,
)
from flowstat.solver.solve import Status, check

CAP = 16  # alternatives kept at a step
CHECKS = 8  # solver queries per alternative kept, at most, for one step
MAX_SIZE = 300  # a condition larger than this is replaced by the one of its first step


@dataclass(frozen=True, slots=True)
class Critical:
    priority: dict[str, float]  # step id -> the length of the longest feasible chain from it
    cut: frozenset[str]  # steps where alternatives were dropped (a lower bound above them)


def priorities(
    plan: Plan,
    signatures: Signatures,
    durations: Mapping[str, float] | None = None,
    inputs: Mapping[str, object] | None = None,
    cap: int = CAP,
) -> Critical:
    weight = durations or {}
    guards = lower_guards(plan, signatures)
    known = {f"inputs.{name}": value for name, value in (inputs or {}).items()}
    preds = {step: restrict(p, known) for step, p in guards.preds.items()}
    # the upper bound of the condition: what the solver cannot read may hold
    runs = {step: bounds(p)[1] for step, p in run_conditions(plan, preds).items()}
    below = _descendants(plan)

    alternatives: dict[str, list[tuple[float, Pred]]] = {}
    priority: dict[str, float] = {}
    cut: set[str] = set()
    for step in reversed(plan.steps):  # plan.steps is topological
        own = weight.get(step.id, 1.0)
        kept: list[tuple[float, Pred]] = []
        if _possible(runs[step.id], guards.domains):
            candidates = [(own, runs[step.id])]
            for later in below[step.id]:
                for length, condition in alternatives[later]:
                    both = conj([runs[step.id], condition])
                    if size(both) > MAX_SIZE:
                        both = runs[step.id]
                    candidates.append((own + length, both))
            kept, stopped = _best(candidates, guards.domains, cap, runs[step.id])
            if stopped:
                cut.add(step.id)
        alternatives[step.id] = kept
        priority[step.id] = kept[0][0] if kept else own
    return Critical(priority, frozenset(cut))


def _best(
    candidates: list[tuple[float, Pred]], domains: dict[str, Domain], cap: int, first: Pred
) -> tuple[list[tuple[float, Pred]], bool]:
    """The longest feasible alternatives, at most ``cap``, and whether any were left out.

    A candidate with the condition of a longer one that is kept adds nothing and is not counted
    as left out. The queries to the solver are limited too: running out of them is a cut. The
    longest candidate that was left out stays, under ``first`` (the condition of the step itself,
    weaker than its own), so that a cut can only make a priority too long, never too short.
    """
    candidates.sort(key=lambda c: -c[0])  # stable: the order of declaration breaks ties
    kept: list[tuple[float, Pred]] = []
    conditions: set[Pred] = set()
    budget = cap * CHECKS
    for length, condition in candidates:
        if condition in conditions:
            continue
        if len(kept) == cap or budget == 0:
            kept.append((length, first))
            kept.sort(key=lambda c: -c[0])
            return kept, True
        budget -= 1
        if _possible(condition, domains):
            kept.append((length, condition))
            conditions.add(condition)
    return kept, False


def _possible(p: Pred, domains: dict[str, Domain]) -> bool:
    if p == TRUE:
        return True
    if p == FALSE:
        return False
    return check(p, dict(domains)).status is not Status.UNSAT


def _descendants(plan: Plan) -> dict[str, list[str]]:
    below: dict[str, set[str]] = {s.id: set() for s in plan.steps}
    for step in reversed(plan.steps):
        for need in step.needs:
            below[need] |= below[step.id] | {step.id}
    order = {s.id: i for i, s in enumerate(plan.steps)}
    return {s: sorted(found, key=order.__getitem__) for s, found in below.items()}


def restrict(p: Pred, known: Mapping[str, object]) -> Pred:
    """``p`` with the variables whose values are known decided; the rest is kept as it is."""
    if not known:
        return p
    match p:
        case Const():
            return p
        case Not(item=item):
            inner = restrict(item, known)
            return Const(not inner.value) if isinstance(inner, Const) else Not(inner)
        case And(items=items):
            return conj([restrict(i, known) for i in items])
        case Or(items=items):
            parts = [restrict(i, known) for i in items]
            if any(x == TRUE for x in parts):
                return TRUE
            rest = tuple(x for x in parts if x != FALSE)
            return FALSE if not rest else rest[0] if len(rest) == 1 else Or(rest)
        case _:
            names = variables(p)
            if names and names <= known.keys():
                decided = holds(p, known)
                if decided is not None:
                    return Const(decided)
            return p
