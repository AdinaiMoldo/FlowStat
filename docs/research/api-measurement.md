# Cost of a request and of a deployment (M6)

Measured on 8 November 2026 with `benchmarks/bench_api.py` (one core of a cloud container, Python
3.13, SQLite on a local file, the example workflow of 25 steps). What is measured and what is not:

- A request goes through the test client of FastAPI **in this process**. The figures are the work
  of the app (token lookup, a SQLite connection, the service, the response) and contain no network
  and no uvicorn.
- "Start a run" is the request plus the run itself up to the approval, because the test pool runs the
  job before the reply. In a real server the request returns after the checks and the run is driven
  on a thread.
- A deployment is a real process of uvicorn, measured from the request to the first answer of
  `/health`.

## Requests

| request | n | median ms | p95 ms |
| --- | --- | --- | --- |
| `GET /health` (no token) | 100 | 2.0 | 2.8 |
| `GET /workflows` | 100 | 2.9 | 4.2 |
| `POST /validate` (the example, 25 steps) | 100 | 8.6 | 11.2 |
| start a run, to the approval | 30 | 74.7 | 88.0 |
| `GET /runs/{id}` (the report) | 100 | 8.5 | 13.6 |

The 2 ms of `/health` is the floor of the framework. A token adds about 1 ms (a connection, the
lookup by hash). Validating and reporting cost 8 to 9 ms because both **compile the text again**: the
plan has no stored form (gap 10 of the audit), so `get_plan` reads the version and compiles it, which
is the 8.6 ms of `/validate`.

## Listing runs is linear and not cheap

| runs stored | `GET /runs` median ms |
| --- | --- |
| 1 | 7.8 |
| 10 | 57.5 |
| 25 | 150.9 |
| 50 | 274.6 |

About 5.5 ms per run: the list builds the report of every run to give its status, and each report
compiles the plan. This is the one figure of the milestone that is not acceptable for a large
installation, and the cause is known. Two changes would remove it: keep the compiled plan of a
published version in the app (a published version never changes, and neither does its snapshot), and
let the list read the status from the events without a plan. Neither is done: the story's criteria
do not need them and the size of this project's data does not either. It is carried to M8, where the
app is made to start with many runs.

## Many runs at once

| pool | runs | seconds | runs per second |
| --- | --- | --- | --- |
| 1 thread | 12 | 0.87 | 13.8 |
| 4 threads | 12 | 0.78 | 15.5 |

Four threads are 12% faster than one, not four times. A run of the example spends its time in the
Python of the engine and in the writes of the log (`synchronous = FULL`, one SQLite file), and Python
threads share one interpreter lock. The pool is there to keep a slow action (a form on a network
disk, an HTTP call) from blocking the other runs, not to speed up the engine itself, and these
actions are not slow here. The measurement does not show that gain and is not claimed to.

## Deployment

| deployment (process) | seconds |
| --- | --- |
| request to the first `/health`, run 1 | 0.56 |
| run 2 | 0.53 |
| run 3 | 0.42 |

Half a second, of which most is the start of the interpreter and the import of FastAPI. The bundle
of a process is 1.8 KiB (text, snapshot, manifest, an empty `data/`); that of a container is 418 KiB
because it holds a copy of the package.

The container path was not timed: the sandbox has no daemon. The test that builds and runs the
image ran on the Ubuntu runner of CI and passed, but CI does not report times, and the first build
installs the packages from the network, so a figure from there would say little about the code.

## What this does not show

No figure here includes a network, TLS, many clients at the same time, or a disk that is not local.
They are a statement of what the code costs, not of how a server would be dimensioned.
