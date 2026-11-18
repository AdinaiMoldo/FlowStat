# Algorithm cards

Every C function has two levels: a known algorithm with our transformation, and a second algorithm that works on the result of the first. One card per function; each card ends with the input on which the plain textbook version fails.

| Function | Level 1 | Level 2 | Level 2 uses |
| --- | --- | --- | --- |
| C1 | Expression parser and shape validator | Semantic phases: foreach expansion, shortest cycle, type inference | the tree and diagnostics of level 1 |
| C6 | Difference-graph solver (Bellman-Ford, strict edges) | Test generator (greedy set cover) | proved targets and witnesses |
| C2 | Guarded dataflow fixpoint | Pruning of infeasible paths | the solver of C6 |
| C3 | DAG scheduler with four join rules | Feasible critical path | C2 and C6 |
| C4 | Write-ahead log and replay | Saga compensation plan | the replayed state |
| C5 | Trace conformance | Epoch-fence handover | the conformance verdict |
| A2 (US-55) | Versions kept with their findings, file hash | One-to-one matching of findings by code and indicator | the findings kept with each version |
| B1 (US-54) | Exact decimal reading of cells and one pass over the rows | Relations and sums over the figures that no rule refused | the figures kept by the first pass |
