# Reuse ledger

For every component: source, licence, what we take, what we write, counted as ours or not.

## Allow-list for C1-C6 packages

Mirrored in `tests/architecture/rules.py`. Changing it needs a journal entry.

| Package | Allowed third-party imports |
| --- | --- |
| `model`, `dsl`, `solver`, `compiler`, `analysis`, `engine`, `recovery`, `migration` | none, except `ruamel.yaml` in `dsl/reader.py` |
| `repo`, `actions`, `api`, `deploy` | components of categories A and B from the table below |

## Components

Versions are those read for the Project Proposal (section 11.4); exact versions go into the lock file at the start of I1.

| Component | Used for | Licence | Version read | Category |
| --- | --- | --- | --- | --- |
| ruamel.yaml | Reading YAML text with line and column positions (B1) | MIT | 0.19.1 | B |
| FastAPI | REST API and OpenAPI documentation (B4) | MIT | 0.162.0 | B |
| Pydantic | Request and response models (B4) | MIT | 2.14.2 | B |
| uvicorn | Server of the core and of the fallback service (B4, B5) | BSD-3-Clause | 0.54.0 | B |
| Jinja2 | Template of the generated service (B5) | BSD | 3.1.6 | B |
| Docker SDK for Python | Build and start containers (B5) | Apache-2.0 | 7.2.0 | B |
| openpyxl | Read and write xlsx files (B6) | MIT | 3.1.5 | B |
| httpx2 | The test client of Starlette and FastAPI (tests only); the action http.call uses `urllib` from the standard library | BSD-3-Clause | 2.13.1 | B |
| Monaco Editor | Code editor in the IDE (B2) | MIT | 0.57.0 | B |
| Cytoscape.js | Graph preview (B3) | MIT | 3.34.4 | B |
| cytoscape-dagre, dagre | Layout of the graph preview (B3) | MIT | 4.0.1, 0.8.5 | B |
| pytest | Test runner | MIT | 9.2.0 | test only |
| Hypothesis | Property-based tests | MPL-2.0 | 6.170.14 | test only |
| StatGuard | The action statguard.validate (B6) | own earlier module | no version | reused, not counted |

Not used on purpose: Lark, jsonschema, Z3, sympy, python-constraint, networkx, Airflow, Celery, Temporal.

Note (5 Nov 2026): pandas was planned for aggregation in B6 and is not used. `stats.aggregate` is a sum by code over a few thousand rows, which the standard library does in a dozen lines, so the dependency (and its install time on the Windows and Linux CI runs) is dropped. openpyxl is also a test dependency, because the tests build synthetic regional forms with it.
