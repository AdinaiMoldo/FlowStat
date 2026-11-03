"""Tests of the graph algorithms of the compiler (C1, level 2): Tarjan, shortest cycle, ancestors.

The oracle for every property is a deliberately naive implementation (reachability by repeated
search, cycles by enumerating all simple paths) written independently of the code under test.
"""

from __future__ import annotations

import random

from flowstat.compiler.graph import (
    ancestor_bitsets,
    shortest_cycle,
    strongly_connected_components,
    topological_order,
)

Adj = list[list[int]]


# -- naive oracles ---------------------------------------------------------------------------
def reach(adj: Adj, start: int) -> set[int]:
    seen = {start}
    stack = [start]
    while stack:
        v = stack.pop()
        for w in adj[v]:
            if w not in seen:
                seen.add(w)
                stack.append(w)
    return seen


def naive_components(adj: Adj) -> set[frozenset[int]]:
    n = len(adj)
    reach_of = [reach(adj, v) for v in range(n)]
    comps: set[frozenset[int]] = set()
    for v in range(n):
        comps.add(frozenset(w for w in reach_of[v] if v in reach_of[w]))
    return comps


def naive_shortest_cycle_length(adj: Adj) -> int | None:
    """Length of the shortest directed cycle, by trying every simple path (small graphs only)."""
    n = len(adj)
    best: int | None = None
    for start in range(n):
        stack: list[tuple[int, tuple[int, ...]]] = [(start, (start,))]
        while stack:
            v, path = stack.pop()
            for w in adj[v]:
                if w == start:
                    if best is None or len(path) < best:
                        best = len(path)
                elif w not in path and (best is None or len(path) + 1 < best):
                    stack.append((w, (*path, w)))
    return best


def random_graph(rng: random.Random, n: int, p: float) -> Adj:
    return [[w for w in range(n) if rng.random() < p] for _ in range(n)]


def random_dag(rng: random.Random, n: int, p: float) -> Adj:
    order = list(range(n))
    rng.shuffle(order)
    rank = {v: i for i, v in enumerate(order)}
    return [[w for w in range(n) if rank[w] > rank[v] and rng.random() < p] for v in range(n)]


# -- strongly connected components -----------------------------------------------------------------
def test_scc_of_small_graphs() -> None:
    # 0 -> 1 -> 2 -> 0, 2 -> 3, 3 alone, 4 self-loop
    adj = [[1], [2], [0, 3], [], [4]]
    comps = {frozenset(c) for c in strongly_connected_components(adj)}
    assert comps == {frozenset({0, 1, 2}), frozenset({3}), frozenset({4})}


def test_scc_of_a_chain_is_all_singletons() -> None:
    adj = [[1], [2], [3], []]
    assert all(len(c) == 1 for c in strongly_connected_components(adj))


def test_scc_empty_graph() -> None:
    assert strongly_connected_components([]) == []


def test_scc_matches_the_naive_oracle_on_random_graphs() -> None:
    seed = 11
    rng = random.Random(seed)
    for _ in range(400):
        n = rng.randint(0, 9)
        adj = random_graph(rng, n, rng.choice([0.1, 0.2, 0.35]))
        got = {frozenset(c) for c in strongly_connected_components(adj)}
        assert got == naive_components(adj), f"seed={seed} adj={adj}"


def test_scc_does_not_recurse_on_a_long_chain() -> None:
    n = 20000
    adj = [[i + 1] for i in range(n - 1)] + [[]]
    assert len(strongly_connected_components(adj)) == n


# -- shortest cycle -------------------------------------------------------------------------------
def test_shortest_cycle_picks_the_shortest_in_a_component() -> None:
    # two cycles through 0: 0-1-2-3-0 (length 4) and 0-4-0 (length 2)
    adj = [[1, 4], [2], [3], [0], [0]]
    cycle = shortest_cycle(adj, [0, 1, 2, 3, 4])
    assert sorted(cycle) == [0, 4]


def test_shortest_cycle_of_a_self_loop() -> None:
    assert shortest_cycle([[0]], [0]) == [0]


def test_shortest_cycle_returns_a_real_cycle() -> None:
    adj = [[1], [2], [0]]
    cycle = shortest_cycle(adj, [0, 1, 2])
    assert len(cycle) == 3
    for a, b in zip(cycle, cycle[1:] + cycle[:1], strict=True):
        assert b in adj[a]


def test_shortest_cycle_length_matches_the_naive_oracle() -> None:
    seed = 12
    rng = random.Random(seed)
    checked = 0
    for _ in range(500):
        n = rng.randint(1, 7)
        adj = random_graph(rng, n, rng.choice([0.15, 0.3]))
        expected = naive_shortest_cycle_length(adj)
        comps = [c for c in strongly_connected_components(adj) if len(c) > 1 or c[0] in adj[c[0]]]
        if expected is None:
            assert comps == [], f"seed={seed} adj={adj}"
            continue
        lengths = []
        for comp in comps:
            cycle = shortest_cycle(adj, comp)
            for a, b in zip(cycle, cycle[1:] + cycle[:1], strict=True):
                assert b in adj[a], f"not a cycle: seed={seed} adj={adj} cycle={cycle}"
            assert len(set(cycle)) == len(cycle)
            lengths.append(len(cycle))
        assert min(lengths) == expected, f"seed={seed} adj={adj}"
        checked += 1
    assert checked > 100


def test_the_plain_textbook_answer_is_not_the_shortest_cycle() -> None:
    """A single DFS finds *a* cycle, here the long one; we must report the short one.

    Edges are listed so that a depth-first search from 0 goes through 1, 2, 3 and closes the
    4-cycle first, although 0 -> 5 -> 0 is shorter.
    """
    adj = [[1, 5], [2], [3], [0], [], [0]]
    cycle = shortest_cycle(adj, [0, 1, 2, 3, 5])
    assert sorted(cycle) == [0, 5]


# -- topological order and ancestors -------------------------------------------------------------
def test_topological_order_respects_every_edge() -> None:
    seed = 13
    rng = random.Random(seed)
    for _ in range(300):
        adj = random_dag(rng, rng.randint(0, 12), 0.3)
        order = topological_order(adj)
        assert order is not None
        assert sorted(order) == list(range(len(adj)))
        position = {v: i for i, v in enumerate(order)}
        for v, targets in enumerate(adj):
            for w in targets:
                assert position[v] < position[w], f"seed={seed} adj={adj}"


def test_topological_order_of_a_cyclic_graph_is_none() -> None:
    assert topological_order([[1], [0]]) is None


def test_ancestor_bitsets_equal_reverse_reachability() -> None:
    seed = 14
    rng = random.Random(seed)
    for _ in range(300):
        n = rng.randint(0, 12)
        adj = random_dag(rng, n, 0.3)
        order = topological_order(adj)
        assert order is not None
        anc = ancestor_bitsets(adj, order)
        for v in range(n):
            expected = {u for u in range(n) if u != v and v in reach(adj, u)}
            got = {u for u in range(n) if anc[v] >> u & 1}
            assert got == expected, f"seed={seed} adj={adj} v={v}"


def test_ancestors_of_a_diamond() -> None:
    adj = [[1, 2], [3], [3], []]
    order = topological_order(adj)
    assert order is not None
    anc = ancestor_bitsets(adj, order)
    assert anc[3] == 0b0111
    assert anc[0] == 0
