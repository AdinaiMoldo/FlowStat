# FlowStat: engineering notes

- Documentation and code comments are in English.
- Read `docs/architecture.md` before adding a module. Imports point down the layers; `tests/architecture` fails otherwise. A new package must be placed on a layer in `tests/architecture/rules.py`.
- C1-C6 packages (`model`, `dsl`, `solver`, `compiler`, `analysis`, `engine`, `recovery`, `migration`) use the standard library and our own code only. The single exception is `ruamel.yaml` in `dsl/reader.py`. Do not add Lark, jsonschema, networkx, Z3 or sympy.
- Test before code for C1, C3, C4 and C6. A complex function is closed only with its algorithm card in `docs/algorithms/`, tests, and a benchmark or measurement.
- Deterministic mode: time, identifiers and the random seed are passed into the engine, never read from global state.
- Only synthetic data in the repository. `.env` and databases are ignored.
- `main` is always green. One branch per story, the story ID in the commit message.
