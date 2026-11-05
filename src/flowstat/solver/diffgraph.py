"""Difference constraints (C6): Bellman-Ford with strict edges.

A constraint ``x_b - x_a <= c`` is an edge ``a -> b`` of weight ``c``; a strict one (``< c``) has
weight ``c - delta`` for an infinitesimal ``delta > 0``. A weight is therefore a pair
``(c, s)`` meaning ``c + s * delta`` with ``s`` the negated number of strict steps, and pairs are
compared lexicographically. The system has a solution exactly when no cycle has a negative total
(a cycle with total ``(0, -k)`` for ``k > 0`` is negative: ``0 < 0`` cannot hold).

Bellman-Ford finds the shortest distance from a virtual source that reaches every node with
weight zero; those distances are a solution. Replacing ``delta`` by a small enough rational gives
exact values.
"""

from __future__ import annotations

from fractions import Fraction

Edge = tuple[int, int, Fraction, int]  # source, target, constant, steps (0, or -1 when strict)


def solve_differences(nodes: int, edges: list[Edge]) -> list[Fraction] | None:
    """Values of ``nodes`` variables that satisfy every edge, or ``None`` on a negative cycle."""
    dist: list[tuple[Fraction, int]] = [(Fraction(0), 0)] * nodes
    for _ in range(nodes + 1):
        changed = False
        for a, b, c, s in edges:
            da = dist[a]
            candidate = (da[0] + c, da[1] + s)
            if candidate < dist[b]:
                dist[b] = candidate
                changed = True
        if not changed:
            break
    else:
        return None  # still improving after |V| rounds: a negative cycle
    return _concretise(dist, edges)


def _concretise(dist: list[tuple[Fraction, int]], edges: list[Edge]) -> list[Fraction]:
    """Pick the smallest delta the edges allow, and evaluate ``c + s * delta``."""
    delta = Fraction(1)
    for a, b, c, s in edges:
        gap = dist[b][0] - dist[a][0] - c  # <= 0 after convergence
        slope = dist[b][1] - dist[a][1] - s
        if gap < 0 and slope > 0:
            delta = min(delta, -gap / slope)
    return [c + s * delta for c, s in dist]
