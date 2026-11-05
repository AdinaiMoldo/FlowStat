"""Tests of the difference graph (C6): Bellman-Ford with strict edges, against Floyd-Warshall."""

from __future__ import annotations

import random
from fractions import Fraction as F

from flowstat.solver.diffgraph import solve_differences
from tests.support.floyd import has_negative_cycle


def edge(a: int, b: int, c: int | F, strict: bool = False) -> tuple[int, int, F, int]:
    return (a, b, F(c), -1 if strict else 0)


def test_no_edges_is_satisfiable() -> None:
    assert solve_differences(3, []) is not None


def test_a_negative_cycle_is_unsatisfiable() -> None:
    # x1 - x0 <= 2, x0 - x1 <= -3
    assert solve_differences(2, [edge(0, 1, 2), edge(1, 0, -3)]) is None


def test_a_zero_cycle_is_satisfiable_unless_an_edge_is_strict() -> None:
    assert solve_differences(2, [edge(0, 1, 2), edge(1, 0, -2)]) is not None
    assert solve_differences(2, [edge(0, 1, 2, True), edge(1, 0, -2)]) is None
    assert solve_differences(2, [edge(0, 1, 2), edge(1, 0, -2, True)]) is None


def test_a_long_negative_cycle() -> None:
    edges = [edge(i, (i + 1) % 6, 1) for i in range(5)] + [edge(5, 0, -5, True)]
    assert solve_differences(6, edges) is None
    edges[-1] = edge(5, 0, -5)
    assert solve_differences(6, edges) is not None


def test_a_solution_satisfies_every_edge() -> None:
    edges = [edge(0, 1, 3), edge(1, 2, -1, True), edge(2, 0, 0), edge(0, 2, 5)]
    values = solve_differences(3, edges)
    assert values is not None
    for a, b, c, s in edges:
        if s:
            assert values[b] - values[a] < c
        else:
            assert values[b] - values[a] <= c


def test_a_strict_chain_gets_distinct_values_between_the_bounds() -> None:
    # 0 < x1 < x2 < x3 < 1 with node 0 as the zero: x_i - z > 0, x2 - x1 > 0 ...
    edges = [
        edge(1, 0, 0, True),  # z - x1 < 0
        edge(2, 1, 0, True),  # x1 - x2 < 0
        edge(3, 2, 0, True),  # x2 - x3 < 0
        edge(0, 3, 1, True),  # x3 - z < 1
    ]
    values = solve_differences(4, edges)
    assert values is not None
    z = values[0]
    xs = [values[i] - z for i in (1, 2, 3)]
    assert 0 < xs[0] < xs[1] < xs[2] < 1


def test_the_witness_is_exact() -> None:
    values = solve_differences(2, [edge(0, 1, F(1, 3), True), edge(1, 0, F(-1, 4))])
    assert values is not None
    assert all(isinstance(v, F) for v in values)
    assert F(1, 4) <= values[1] - values[0] < F(1, 3)


def _random_system(rng: random.Random) -> tuple[int, list[tuple[int, int, F, int]]]:
    n = rng.randint(2, 6)
    edges = []
    for _ in range(rng.randint(1, 10)):
        a, b = rng.randrange(n), rng.randrange(n)
        if a != b:
            edges.append(
                edge(a, b, F(rng.randint(-6, 6), rng.choice([1, 1, 2])), rng.random() < 0.4)
            )
    return n, edges


def test_bellman_ford_agrees_with_floyd_warshall_on_random_systems() -> None:
    rng = random.Random(20261105)
    satisfiable = 0
    for _ in range(600):
        n, edges = _random_system(rng)
        values = solve_differences(n, edges)
        assert (values is None) == has_negative_cycle(n, edges), (n, edges)
        if values is not None:
            satisfiable += 1
            for a, b, c, s in edges:
                diff = values[b] - values[a]
                assert diff < c if s else diff <= c, (n, edges, values)
    assert 150 < satisfiable < 550  # the sample has both kinds


def test_unsatisfiable_by_brute_force_on_small_integer_systems() -> None:
    """Integer systems with integer constants: a grid search finds a solution iff one exists."""
    rng = random.Random(7)
    for _ in range(200):
        n = rng.randint(2, 4)
        edges = [
            edge(a, b, rng.randint(-3, 3))
            for a in range(n)
            for b in range(n)
            if a != b and rng.random() < 0.5
        ]
        values = solve_differences(n, edges)
        grid_sat = any(
            all(xs[b] - xs[a] <= c for a, b, c, _ in edges) for xs in _grid(n, range(-8, 9))
        )
        assert (values is not None) == grid_sat, (n, edges)


def _grid(n: int, rng_: range) -> list[list[int]]:
    """Assignments with node 0 fixed at 0 (differences only matter)."""
    out: list[list[int]] = [[0]]
    for _ in range(n - 1):
        out = [[*xs, v] for xs in out for v in rng_]
    return out
