# Traceability matrix

Row per requirement: UR or DR, user story, function (A/B/C), design element, test, result. Filled from I1 and finalised in I6.

| UR / DR | Story | Function | Design element | Test | Result |
| --- | --- | --- | --- | --- | --- |
| UR-01 (the designer works in versions), DR-03 | US-01 | A1 | `repo/versions.py`, triggers in `repo/db.py` | `tests/unit/repo/test_versions.py`, `tests/acceptance/test_us01_us02.py` | passed, M1 |
| UR-01, DR-03 (a published version always means the same graph) | US-02 | A4 | `repo/catalogue.py`, `compiler/inputs.py`, `service/workflows.py` (catalogue snapshot) | `tests/unit/repo/test_catalogue_repo.py`, `tests/unit/compiler/test_inputs.py`, `tests/acceptance/test_us01_us02.py` | passed, M1 |
| UR-01 (errors before a run) | US-03 | B1 | `dsl/reader.py`, `dsl/shape.py`, `dsl/lexer.py`, `dsl/parser.py` | `tests/unit/dsl/`, `tests/acceptance/test_us03_us05.py` | passed, M1 |
| UR-01 | US-04 | C1 | `compiler/compile.py`, `compiler/graph.py` | `tests/unit/compiler/`, `tests/acceptance/test_us03_us05.py` | passed, M1 |
| UR-01, DR-03 (the graph is static) | US-05 | C1 | `compiler/compile.py` phase 0 | `tests/unit/compiler/test_compile.py`, `tests/acceptance/test_us03_us05.py` | passed, M1 |
| NFR-01 (compile 200 steps in at most 1 s) | US-04, US-05 | C1 | `compiler/` | `benchmarks/bench_compile.py`, `tests/unit/compiler/test_performance.py` | passed, 68 ms for 200 steps |
| UR-02 (independent steps run in parallel) | US-06 | C3 | `engine/scheduler.py`, `engine/simulation.py` | `tests/unit/engine/test_scheduler.py`, `tests/unit/engine/test_properties.py`, `tests/acceptance/test_us06_us10.py` | passed, M2 |
| UR-02 (skips, failures and quorum) | US-07 | C3 | `engine/scheduler.py` (join rules, failure policy) | `tests/unit/engine/test_scheduler.py`, `tests/unit/engine/test_properties.py` (oracle), `tests/acceptance/test_us06_us10.py` | passed, M2 |
| UR-02, DR-04 (the chief statistician approves) | US-08 | C3 | `engine/scheduler.py` (WAITING, `signal`) | `tests/unit/engine/test_scheduler.py`, `tests/acceptance/test_us06_us10.py` | passed, M2 |
| UR-02, DR-02 (rejection is data, no false success) | US-09 | C3 | `engine/scheduler.py` (outcome), `model/states.py` | `tests/unit/engine/test_scheduler.py`, `tests/acceptance/test_us06_us10.py` | passed, M2 |
| UR-02 (a broken contract is a failure) | US-10 | C3, C1 | `engine/scheduler.py` (`ensures`), `engine/evaluator.py` | `tests/unit/engine/test_evaluator.py`, `tests/acceptance/test_us06_us10.py` | passed, M2 |
| UR-02 (an explicit result for every run) | US-11 | A2 | `repo/runs.py`, `model/history.py`, `service/runs.py` | `tests/unit/repo/test_runs.py`, `tests/unit/model/test_history.py`, `tests/unit/service/test_runs.py`, `tests/acceptance/test_us11.py` | passed, M2 |
| UR-02 (a run uses the department's files and database) | US-12 | B6 | `actions/` (`xlsx`, `statguard`, `stats`, `database`, `email`, `registry`), `engine/runner.py`, `service/cli.py` | `tests/unit/actions/`, `tests/unit/engine/test_runner.py`, `tests/unit/service/test_cli.py`, `tests/acceptance/test_us12.py` | passed, M2 |
