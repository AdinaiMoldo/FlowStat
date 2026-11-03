"""Graph algorithms of the compiler (C1, level 2), on graphs given as adjacency lists of indexes.

* ``strongly_connected_components``: Tarjan, written with an explicit stack so that a chain of
  tens of thousands of steps does not exhaust the Python recursion limit.
* ``shortest_cycle``: a breadth-first search from every node of a component. The textbook way of
  reporting a cycle (the one a depth-first search happens to close) can be much longer than the
  shortest one, and the shortest is the one a designer can read and fix.
* ``topological_order`` and ``ancestor_bitsets``: ancestors of every node as one integer whose bit
  ``u`` is set when ``u`` can reach the node; building them in topological order costs one OR per
  edge.
"""

from __future__ import annotations

from collections import deque

Adj = list[list[int]]


def strongly_connected_components(adj: Adj) -> list[list[int]]:
    n = len(adj)
    index_of = [-1] * n
    low = [0] * n
    on_stack = [False] * n
    stack: list[int] = []
    components: list[list[int]] = []
    counter = 0
    for root in range(n):
        if index_of[root] != -1:
            continue
        work: list[tuple[int, int]] = [(root, 0)]
        while work:
            v, child = work.pop()
            if child == 0:
                index_of[v] = low[v] = counter
                counter += 1
                stack.append(v)
                on_stack[v] = True
            recursed = False
            while child < len(adj[v]):
                w = adj[v][child]
                child += 1
                if index_of[w] == -1:
                    work.append((v, child))
                    work.append((w, 0))
                    recursed = True
                    break
                if on_stack[w]:
                    low[v] = min(low[v], index_of[w])
            if recursed:
                continue
            if low[v] == index_of[v]:
                component: list[int] = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    component.append(w)
                    if w == v:
                        break
                components.append(component)
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[v])
    return components


def shortest_cycle(adj: Adj, component: list[int]) -> list[int]:
    """The shortest directed cycle inside one strongly connected component.

    Returns the nodes in order, without repeating the first one at the end.
    """
    members = set(component)
    best: list[int] | None = None
    for start in component:
        parent: dict[int, int] = {start: start}
        queue = deque([start])
        found: int | None = None
        while queue and found is None:
            v = queue.popleft()
            for w in adj[v]:
                if w not in members:
                    continue
                if w == start:
                    found = v
                    break
                if w not in parent:
                    parent[w] = v
                    queue.append(w)
        if found is None:
            continue
        path = [found]
        while path[-1] != start:
            path.append(parent[path[-1]])
        path.reverse()
        if best is None or len(path) < len(best):
            best = path
            if len(best) == 1:
                break
    assert best is not None, "a component with a cycle was expected"
    return best


def topological_order(adj: Adj) -> list[int] | None:
    """Kahn's algorithm. Returns None when the graph has a cycle."""
    indegree = [0] * len(adj)
    for targets in adj:
        for w in targets:
            indegree[w] += 1
    queue = deque(v for v, d in enumerate(indegree) if d == 0)
    order: list[int] = []
    while queue:
        v = queue.popleft()
        order.append(v)
        for w in adj[v]:
            indegree[w] -= 1
            if indegree[w] == 0:
                queue.append(w)
    return order if len(order) == len(adj) else None


def ancestor_bitsets(adj: Adj, order: list[int]) -> list[int]:
    """For every node, the set of nodes that can reach it, as an integer bitset."""
    ancestors = [0] * len(adj)
    for v in order:
        mask = ancestors[v] | (1 << v)
        for w in adj[v]:
            ancestors[w] |= mask
    return ancestors
