# Architecture

## One engine, two roles

The core runs workflows during development and testing with an embedded executor. A deployed
service is a container with the same engine, a frozen plan of one version (the compiled graph and a
snapshot of the catalogue) and its own write-ahead event log. That log is the source of truth for
the runs the service owns. An outbox delivers events to the core at least once; the core stores them
idempotently by the pair (run, sequence number). History and statistics are read from that copy and
may lag. A run changes owner only through migration (C5), behind an epoch fence.

## Layers

A module may import only from a package on a lower layer or from its own package. Packages on the
same layer do not import each other. `tests/architecture/test_layers.py` enforces this.

```text
layer 8   api            deploy
layer 7   migration        service        C5 / orchestration of repo, compiler and later layers
layer 6   actions        recovery        B6 / C4
layer 5   engine                         C3
layer 4   analysis                       C2, feasible critical path
layer 3   compiler                       C1 phases 0-4
layer 2   solver         repo            C6 / A1-A6
layer 1   dsl                            B1 reader, C1 lexer, parser, shape validator
layer 0   model                          shared data types
```

The engine knows nothing about the IDE, the API, the repository or the actions: it receives them as
protocols (`engine/ports.py`) and the higher layers implement them. An import from below upwards is an
architecture bug, not a style issue.

## Third-party libraries

The packages `model`, `dsl`, `solver`, `compiler`, `analysis`, `engine`, `recovery` and `migration`
import only the standard library and our own code. The single exception is `ruamel.yaml` in
`dsl/reader.py`. `tests/architecture/test_allowed_imports.py` enforces this; the allow-list is
mirrored in `reuse.md`. Components of categories A and B (FastAPI, Docker, openpyxl, ...) live in
`api`, `deploy`, `actions` and `repo`.

## Invariants

| Invariant | Where enforced | How checked |
| --- | --- | --- |
| A run has exactly one owner at any moment (DR-01) | Epoch fence on migration | Crash at every handover step (NFR-05) |
| Rejection is data: a run that reaches no goal is GOAL_NOT_REACHED, never SUCCEEDED (DR-02) | C3 | US-09 |
| The graph is static: foreach is expanded at publication (DR-03) | C1 phase 0 | Golden test on nine regions |
| A published version is immutable | A1 | US-01 |
| An event is written before it is acknowledged | C4 | Crash at every event |
| An expression outside the analysable fragment is "unknown", never "proven" | C2, C6 | Out-of-fragment cases in property tests |
