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
