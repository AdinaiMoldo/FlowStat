"""Floyd-Warshall over the same weights as the solver (the second implementation of the test).

The product finds a negative cycle with Bellman-Ford (``solver/diffgraph.py``). The tests compare it
with this all-pairs version, which is written differently: a cycle is negative when the shortest
distance from a node to itself is below zero.
"""

from __future__ import annotations

from fractions import Fraction

Edge = tuple[int, int, Fraction, int]  # source, target, constant, number of strict steps (negated)
Weight = tuple[Fraction, int]

ZERO: Weight = (Fraction(0), 0)


def has_negative_cycle(nodes: int, edges: list[Edge]) -> bool:
    inf: Weight | None = None
    dist: list[list[Weight | None]] = [[inf] * nodes for _ in range(nodes)]
    for a, b, c, s in edges:
        w = (c, s)
        current = dist[a][b]
        if current is None or w < current:
            dist[a][b] = w
    for k in range(nodes):
        for i in range(nodes):
            left = dist[i][k]
            if left is None:
                continue
            for j in range(nodes):
                right = dist[k][j]
                if right is None:
                    continue
                total = (left[0] + right[0], left[1] + right[1])
                current = dist[i][j]
                if current is None or total < current:
                    dist[i][j] = total
    return any(d is not None and d < ZERO for d in (dist[i][i] for i in range(nodes)))
