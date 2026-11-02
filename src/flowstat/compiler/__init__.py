"""C1 semantic phases. Layer 3.

Compiles a parsed document into a plan (a static DAG) plus diagnostics.

expand.py     phase 0: foreach expansion with a source map.
symbols.py    phase 1: steps, inputs, catalogue, action registry.
refs.py       phase 2: needs, references, edges, ancestor bitsets.
cycles.py     phase 3: Tarjan SCC, shortest cycle by BFS.
types.py      phase 4: type inference, structural subtyping, fan-in lists, error type.
plan.py       the compiled plan consumed by every layer above.

Phases 0-3 in I1, phase 4 in I4. Algorithm card: docs/algorithms/C1.md.
"""
