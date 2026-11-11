# Negative check

Functions we do not count as complex, with the reason, and the result of the complexity criteria (rule 5) for each C function once it is finished.

## Not counted as complex

- Deploying a workflow as a service: a template plus Docker (B5).
- Shipping events from a service to the core: an outbox and an insert with a unique key (A6).
- Reading YAML text into a tree: ruamel.yaml (B1).
- Cycle detection and topological order: textbook; a phase inside C1 and C3.
- Retries with exponential backoff: a loop; inside C4, not counted.
- Expression evaluation: a tree walk; inside C3.
- Autocompletion pop-up (Monaco), graph drawing (dagre), OpenAPI (FastAPI).
- Run statistics, versioning, role-based access: SQL aggregates, a new row per version, a table lookup.
- Running generated test inputs with mocked actions: ordinary test execution.

## Criteria check per function

The criteria are those of rule 5 of `engineering-rules.md`: a function is complex only with a non-trivial search or iteration, edge cases that are handled separately, and an independent oracle. Filled on 11 November (M9) from the algorithm cards, which have the detail; a number is in this table only if it is in the card or was counted again on that day (the golden documents of C1, the runs of C2 and C3).

| Function | Non-trivial search or iteration | Edge cases handled separately | Independent oracle | Verdict |
| --- | --- | --- | --- | --- |
| C1 compiler | Tarjan components with an explicit stack, a breadth-first search from every node of a component for the shortest cycle, ancestor bitsets over the plan, precedence climbing with error nodes | cycles (a self-loop, two independent ones), `foreach` over an empty list, a quorum against a list that failed, errors of several phases in one run, a chain of 20,000 steps | naive reachability and enumeration of all simple cycles on random graphs; Python's `eval` on generated expressions; 71 golden invalid documents with exact line and column (50 of the shape validator, 21 of the compiler) | complex; all three |
| C2 availability | one pass over the plan in order, in which a predicate is built for every step and the solver is asked for every reference | a step that is skipped, a `continue` step that failed, a quorum met by fewer instances than the reader indexes, the 300-node limit on a fact, `exists()` as a proof | a reference interpreter with its own join table and evaluator, which imports neither the engine nor the analysis: 4 seeds x 150 workflows x 8 runs, soundness and exactness of the optimistic run | complex; all three |
| C3 engine | a ready heap ordered by the feasible critical path, joins and failure policies, the deadlines of waiting steps | a failure that cancels the rest, `continue`, compensation, cancel during an undo, a retry, the time limit | a second evaluator that recomputes every state by repeated passes with no queue, no heap and no time, on random workflows for 1, 2, 3 and 100 workers; the reference interpreter again on 4 seeds x 120 workflows x 4 runs | complex; all three |
| C4 fault tolerance | a fold of the history into the state of a run, the order of an undo from bitsets of descendants, a restart of what was in flight | a call that is not idempotent (UNCERTAIN), a second crash during the recovery, a stop that was half written, an undo that was in flight | a crash after every event of the nine-region example and of sagas, compared with the uninterrupted run. **The weakness is in the card and stays here:** the uninterrupted run comes from the same scheduler, so this oracle shows that a crash changes nothing, not that the scheduler is right; the scheduler is checked by the oracle of C3 | complex; the oracle is independent of the recovery code and not of the scheduler |
| C5 migration | a fold of the history and a test of every step of the old plan against the new one; a protocol of three calls between owners with a fence by epoch | a run that has finished, that is undoing its work, that has an uncertain step, a step that is running, a rename, a changed guard, a handover of a run to the store it is in | the claim "the new plan explains the history" is tested by restoring the history under the new plan, at every cut of seven pairs of versions; the handover by a crash before and after every call, read from the databases alone | complex; all three |
| C6 solver and generator | a DNF with a cap on clauses, sets of intervals and strings, Bellman-Ford with strict edges, integer tightening, a greedy cover improved by an exact search | more clauses than the cap (`unknown`), opaque atoms, strict against closed edges, a case that no input reaches (infeasible) | brute force over a grid on 500 random predicates (exact) and 250 over reals (never wrong); Floyd-Warshall, written separately, against Bellman-Ford; the lowering of a guard against the evaluator of the engine; deliberate faults in the solver that make tests fail | complex; all three |

## What is still not complex, and why

Nothing was moved in either direction by the work of M9. US-36 to US-42 are a restart of a scan (US-36), a state that is shown and not stored (US-37), a deadline added to C3 (US-38, inside C3), a function of a URL (US-39), a two-phase change of a key with a record, which is C5's protocol again on a smaller scale (US-40), a bounded memo (US-41, measured) and a poll (US-42). None of them has a new search with its own oracle; each has its tests and, where a number was the reason, its measurement.
