"""C4, level 2: the order of compensation (a saga).

A step that succeeded is undone only after every step that can be reached from it through
``needs`` and is also undone (its descendants), because those steps may have used its result. The
planner returns, for every step to undo, the steps that must be undone before it. The scheduler
runs the undo calls from this plan (docs/dsl-spec.md 5.4).

The descendants are found with one bitset per step, built from the sinks backwards: the plan is in
topological order, so a single pass is enough. Cost: O(V * (V + E) / w) for words of w bits.
"""

from __future__ import annotations

from collections.abc import Collection

from flowstat.compiler.plan import Plan


def compensation_blockers(plan: Plan, to_undo: Collection[str]) -> dict[str, frozenset[str]]:
    """For each step in ``to_undo``, the steps in ``to_undo`` that must be undone before it."""
    steps = plan.steps
    index = {s.id: i for i, s in enumerate(steps)}
    followers: list[list[int]] = [[] for _ in steps]
    for i, step in enumerate(steps):
        for needed in step.needs:
            followers[index[needed]].append(i)
    below = [0] * len(steps)  # bit j of below[i]: step j can be reached from step i
    for i in reversed(range(len(steps))):
        bits = 0
        for j in followers[i]:
            bits |= (1 << j) | below[j]
        below[i] = bits
    wanted = 0
    for step_id in to_undo:
        wanted |= 1 << index[step_id]  # KeyError for a step the plan does not have
    return {
        step_id: frozenset(steps[j].id for j in _bits(below[index[step_id]] & wanted))
        for step_id in to_undo
    }


def _bits(mask: int) -> list[int]:
    found = []
    while mask:
        low = mask & -mask
        found.append(low.bit_length() - 1)
        mask ^= low
    return found
