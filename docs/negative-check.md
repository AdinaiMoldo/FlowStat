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

## Criteria check per function (filled when the function is finished)

| Function | Non-trivial search or iteration | Edge cases handled separately | Independent oracle | Verdict |
| --- | --- | --- | --- | --- |
| C1 | | | | |
| C2 | | | | |
| C3 | | | | |
| C4 | | | | |
| C5 | | | | |
| C6 | | | | |
