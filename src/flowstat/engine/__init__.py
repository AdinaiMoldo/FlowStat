"""C3 execution engine. Layer 5.

Event-driven DAG scheduler.

machine.py    step state machine and readiness counters.
joins.py      the four join rules (all_success, any_success, all_done, at_least k).
scheduler.py  priority queue, bounded parallelism, cancellation, waiting steps, goals.
evaluator.py  tree-walking evaluator for when and ensures over the AST of the parser.
clock.py      injected clock, identifier source and random seed (deterministic mode).
ports.py      protocols the engine needs: Action, EventSink. Implemented by higher layers.

Never imports actions, the repository or the API: they are injected. Built in I2.
Algorithm card: docs/algorithms/C3.md.
"""
