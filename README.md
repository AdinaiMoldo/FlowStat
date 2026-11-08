# FlowStat

Workflow engine written from scratch: expression parser, type checker, constraint solver, scheduler and write-ahead recovery, no workflow frameworks. Software Engineering course project, University of Messina.

## What it is

A configurable workflow engine for the National Statistical Committee of the Kyrgyz Republic. A designer describes a statistical process (for example, a quarterly consolidation of forms from nine regional offices) in a typed YAML-based DSL inside a web IDE. The system checks the process statically, runs it, resumes it after a crash, and deploys each workflow as a separate service with its own REST API.

## How it works

```text
DSL text -> read YAML (ruamel.yaml) -> shape check -> expression parser -> compile (C1)
         -> analyse (C2, C6) -> run (C3) -> log and recover (C4) -> migrate versions (C5)
```

| | Functions |
| --- | --- |
| A, data | versions, history, roles, catalogue, statistics, service registry |
| B, third-party | Monaco editor, graph preview, FastAPI, Docker, spreadsheets and mail actions |
| C, our algorithms | C1 compiler, C2 availability analysis, C3 engine, C4 fault tolerance, C5 migration, C6 predicate solver |

C1-C6 import only the standard library and our own code; the only exception is `ruamel.yaml`, which reads YAML text. This is enforced by `tests/architecture`.

## Repository layout

```text
src/flowstat/     model, dsl, solver, repo, compiler, analysis, engine, actions,
                  recovery, migration, api, deploy   (layers: docs/architecture.md)
tests/            unit, golden, property, crash, acceptance, reference, architecture
benchmarks/       measurements with conclusions in docs/research
examples/         synthetic workflows and catalogue
docs/             proposal, playbook, architecture, algorithm cards, journal, backlog
scripts/          run the suite in a Linux container
```

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev,api,deploy]"
pytest --cov
```

To use it from the command line, or over HTTP (the contract is `docs/api-spec.md`):

```bash
flowstat user add ada --role admin --db flowstat.db      # prints a token once
python -m flowstat.serve --db flowstat.db --data forms/ --target results.db
```

## Showing the project

One command runs the whole story on synthetic data and prints it (about 5 seconds):

```bash
python scripts/demo.py                  # the service as a process
python scripts/demo.py --kind docker    # the service as a container
```

It makes four users, publishes the example workflow, starts the API, shows that an operator may not
publish and that only the chief statistician may approve, runs the workflow in the core, deploys the
same version as a separate service, runs it there with its own keys and compares the two reports
step by step. `--kind docker` needs Docker with **Linux containers** (on Windows, Docker Desktop in
its default Linux mode; the first build installs the packages and takes a minute or two).
To look at the interactive API documentation instead, start the server (above) and open
`http://127.0.0.1:8000/docs`.

The suite must also pass in a Linux container: `scripts/test-in-linux.sh` (or `.ps1`).

## Process

Six increments, each ending with a working system and a report section: I1 language core, I2 engine, I3 fault tolerance, I4 static analysis, I5 IDE and services, I6 evolution. Rules and definitions of done: `docs/engineering-rules.md` and the Engineering Playbook.
