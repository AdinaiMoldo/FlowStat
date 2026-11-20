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
flowstat user add ada --role admin --password --db flowstat.db  # prints a token once, asks for a password to sign in with
flowstat user add aibek --role operator --region OSH --password --db flowstat.db  # an operator of one regional office
python -m flowstat.serve --db flowstat.db --data forms/ --target results.db
```

The editor is a page of the same server. Fetch it once (about 15 MB, from the npm registry, with its
checksums checked), then open `http://127.0.0.1:8000/ui/` and sign in with a name and a password
(`flowstat user add dina --role designer --password`):

```bash
python scripts/fetch_ui.py
```

The page opens with a sign-in screen (a name and a password; an API token is the other way in). After it come a home page (a desk of the role: workflows for the designer, regions that have sent their forms for the operator, approvals that wait for the chief statistician, the week and the latest runs for the administrator) and the views below. Each has its own address (`#/home`, `#/campaigns/<campaign>`, `#/editor/<workflow>`, `#/forms`, `#/runs/<run>`, `#/services`, `#/migration`, `#/statistics`), so the back and forward buttons, a reload and a link work, and the menu shows only what the role may use. Campaigns: one per quarter of reporting, with its deadline, the regions that are expected, how many forms have come and how many are valid, and the changes of its life cycle that a person may make (an administrator makes and moves them, every role reads them); the forms of a quarter whose result is approved cannot be changed. Forms: the forms of the nine regions for a quarter, sent as Excel files (`python scripts/make_forms.py` makes synthetic sets to send), with the rules each one breaks. Editor: the text with live diagnostics, completion and the graph. Runs: the runs of the core and the runs that deployed services have shipped to it (`GET /runs?deployment=<id>`). Services: what is deployed, which version, where, its status, how many runs it has shipped and when it last sent something; a click shows its runs. A deployed service sends the history of its runs to the core by itself, with a key of its own; `python -m flowstat.serve` tells services where the core is (`--ingest-url`, by default its own address).

Without that step the page still works, in a plain text area (no completion, and the graph is a list of layers). In the editor, type `${` inside a value to get the inputs and the outputs that are available at that step; the button "Show graph" draws the workflow.

The whole story can be shown on the page, with the roles of the office. Start it with `python scripts/exercise_setup.py start --logins --no-forms` (add `--with-history` so that no page is empty: nine runs of the last two weeks, one that waits for the approval, a second version and a service) and open the address it prints. The designer (azamat) loads the example in the Editor, saves and publishes it. An operator (atai) opens Forms and chooses the nine files of `exercise-data/sample-forms/good`: every form passes, and the note says that the run can be started. Run, in the Editor, starts a run of the published version; the Runs view shows it stopping at the approval, with the graph coloured by the state of each step. The chief statistician (alua) sees it on the home page, opens it and presses Approve (a designer is refused, and the page says why); the run ends SUCCEEDED. Then the operator sends the set `with-a-mistake`: the form of BAT has a negative value, the Forms page names it, and the next run stops at `check[BAT]`, which the page of the run says in one red block with the code `E-ACT-005` and the message. To show a goal that is not reached, press "Remove all" and send `six-regions`: three forms are missing and the run ends GOAL_NOT_REACHED. Without `--logins` the page shows no button for a role; with it each user has one. It works only on `127.0.0.1`, because it hands the tokens of the demonstration users to whoever can reach the page.

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
