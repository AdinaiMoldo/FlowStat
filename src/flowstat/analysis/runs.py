"""The condition under which a step runs (the optimistic run of C2, dsl-spec 8.3 and 10.1).

``run_conditions`` gives, for every step, a predicate over the inputs and the outputs that guards
read: the guard of the step and, for its join rule, the conditions of the steps it needs. It
assumes that no action fails. A guard that is outside the fragment stays an ``Opaque`` atom, so a
condition is exact where it has none; ``bounds`` gives the two predicates that enclose it.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping

from flowstat.compiler.plan import JoinSpec, Plan
from flowstat.solver.predicate import (
    FALSE,
    TRUE,
    And,
    Const,
    Not,
    Opaque,
    Or,
    Pred,
    nnf,
)

QUORUM_CAP = 64  # combinations of dependencies that an ``at_least`` may expand to


def run_conditions(plan: Plan, preds: Mapping[str, Pred]) -> dict[str, Pred]:
    runs: dict[str, Pred] = {}
    for step in plan.steps:
        gate = _gate(step.join, [runs[d] for d in step.needs])
        guard = preds.get(step.id)
        runs[step.id] = gate if guard is None else conj([gate, guard])
    return runs


def _gate(join: JoinSpec, deps: list[Pred]) -> Pred:
    if not deps or join.kind == "all_done":
        return TRUE
    if join.kind == "all_success":
        return conj(deps)
    if join.kind == "any_success":
        return disj(deps)
    return _quorum(deps, join.k)


def _quorum(deps: list[Pred], k: int) -> Pred:
    sure = sum(1 for d in deps if d == TRUE)
    open_ = [d for d in deps if d not in (TRUE, FALSE)]
    need = k - sure
    if need <= 0:
        return TRUE
    if need > len(open_):
        return FALSE
    if math.comb(len(open_), need) > QUORUM_CAP:
        return Opaque("a quorum over many guarded steps")
    return disj([conj(list(c)) for c in itertools.combinations(open_, need)])


def conj(items: list[Pred]) -> Pred:
    if FALSE in items:
        return FALSE
    kept = [i for i in items if i != TRUE]
    if not kept:
        return TRUE
    return kept[0] if len(kept) == 1 else And(tuple(kept))


def disj(items: list[Pred]) -> Pred:
    if TRUE in items:
        return TRUE
    kept = [i for i in items if i != FALSE]
    if not kept:
        return FALSE
    return kept[0] if len(kept) == 1 else Or(tuple(kept))


def bounds(p: Pred) -> tuple[Pred, Pred]:
    """Two predicates without opaque atoms: ``low`` implies ``p`` and ``p`` implies ``high``.

    An opaque atom is an unknown truth value; in negation normal form the predicate does not
    decrease when it becomes true, so false gives the lower and true the upper bound.
    """
    q = nnf(p)
    return _fill(q, False), _fill(q, True)


def _fill(p: Pred, value: bool) -> Pred:
    match p:
        case Opaque():
            return Const(value)
        case And(items=items):
            return conj([_fill(i, value) for i in items])
        case Or(items=items):
            return disj([_fill(i, value) for i in items])
        case Not(item=item):  # only in front of nothing: nnf removed them
            return Not(_fill(item, value))
        case _:
            return p


def size(p: Pred) -> int:
    """The number of nodes of a predicate."""
    match p:
        case And(items=items) | Or(items=items):
            return 1 + sum(size(i) for i in items)
        case Not(item=item):
            return 1 + size(item)
        case _:
            return 1


def negate(p: Pred) -> Pred:
    if p == TRUE:
        return FALSE
    if p == FALSE:
        return TRUE
    return nnf(p, True)


__all__ = ["bounds", "conj", "disj", "negate", "run_conditions", "size"]
