# API and deployment contract (M6)

This document is the contract of the REST API of the core and of a deployed workflow service, and of
the way a version is packaged and deployed. The DSL itself is in `dsl-spec.md`. The code of M6 follows
this text; a difference is a bug in one of the two. The OpenAPI file that FastAPI generates is kept
as a golden file (`tests/golden/openapi-core.json`, `openapi-service.json`) so a change of the
contract shows in a diff.

## 1. Who calls

| Caller | Role | Where |
| --- | --- | --- |
| Designer | `designer` | core: writes drafts, validates, publishes, starts test runs |
| Operator | `operator` | core: starts runs, answers approvals |
| Admin | `admin` | core: everything, including deployments and the catalogue |
| External system | `operator` (a machine user) | core, or the service of a deployment, with a token |

There is no separate role for an external system: it is a user whose role is `operator`. A user also
has a set of *titles* (for example `chief_statistician`), which are not roles: they decide who may
answer an approval step (section 5).

## 2. Authentication

`Authorization: Bearer <token>` on every request except `GET /health`.

- A token is 32 random bytes, hex-encoded, made once by `flowstat user add NAME --role ROLE
  [--title T ...]` and printed once. The database keeps only its SHA-256, so a stolen database does
  not give the tokens. A token is looked up by its hash, so there is no comparison of secrets whose
  time could leak. The random bytes come from the command line
  (the edge); no lower layer reads a source of randomness.
- A missing, unknown or disabled token gives 401 `E-AUTH-001`. A role without the permission gives
  403 `E-AUTH-002`. The first admin is made with the same command on the database file; no
  endpoint creates users in M6. `flowstat user disable NAME` stops a token and `flowstat user list`
  shows the users, without tokens.
- There are no passwords, sessions or JWT: the IDE of M7 keeps a token.
- A deployed service has keys made when it is deployed (section 7), shown once and stored as
  SHA-256 in the manifest: one `api_key` that starts runs and reads (the permissions of `operator`,
  no title), and one key for each title that an approval of the workflow names (`title_keys`, for
  the example `chief_statistician`). The same check as in the core applies (section 5): the key
  that starts runs cannot answer an approval, and the key of a title answers only the approvals of
  that title. The answer is recorded as `approver.<title>`. The service has no routes for anything
  else.

## 3. Permissions

One permission is declared per route in code; a route without one is not accepted by the test that
walks the routes. `public` is the single exception (`GET /health`).

| Permission | Routes | admin | designer | operator |
| --- | --- | --- | --- | --- |
| `workflows:read` | `GET /workflows`, `GET /workflows/{name}/versions`, `GET /workflows/{name}/versions/{n}`, `GET /workflows/{name}/versions/{n}/graph` | yes | yes | yes |
| `workflows:write` | `POST /validate`, `POST /graph`, `POST /complete`, `POST /workflows/{name}/versions` | yes | yes | no |
| `workflows:publish` | `POST /workflows/{name}/versions/{n}/publish` | yes | yes | no |
| `catalogue:write` | `PUT /catalogue/{list}` | yes | no | no |
| `runs:read` | `GET /runs`, `GET /runs/{id}` | yes | yes | yes |
| `runs:start` | `POST /workflows/{name}/runs` | yes | yes | yes |
| `runs:decide` | `POST /runs/{id}/decisions` (and the title check, section 5) | yes | no | yes |
| `deployments:read` | `GET /deployments`, `GET /deployments/{id}` | yes | yes | yes |
| `deployments:write` | `POST /deployments`, `DELETE /deployments/{id}` | yes | no | no |

"Publishing is restricted to designers" (US-29) is read as: the role that writes workflows publishes
them, and the admin, who has every permission, can too. An operator gets 403.

## 4. Errors

Every response that is not 2xx has the same body:

```json
{"errors": [{"code": "E-INPUT-003", "field": "inputs.quarter", "message": "...", "line": 12, "column": 5}]}
```

`field` is present for an error about an input or a body field, `line` and `column` for an error in
workflow text (the diagnostics of the compiler, with the same codes and positions as the command
line). A mistake in the shape of the request itself (a missing body field, a wrong JSON type) is
also this body, code `E-API-001`, status 422; FastAPI's own format is replaced.

| Status | Code | When |
| --- | --- | --- |
| 401 | `E-AUTH-001` | no, unknown or disabled token |
| 403 | `E-AUTH-002` | the role lacks the permission, or the title for the approval is missing |
| 404 | `E-API-002` | workflow, version, run or deployment not found |
| 409 | `E-API-003` | the version is a draft (not published); the run is being driven right now; the step is not waiting; the deployment is not running |
| 422 | `E-API-001` | the body does not have the shape |
| 422 | `E-INPUT-001`..`008` | invalid run inputs, one error per input (the codes of `compiler/inputs.py`) |
| 422 | `E-API-004` | the text has errors and cannot be published; `errors` holds the diagnostics |
| 422 | `E-API-005` | the decision is not one the step declares |
| 405 | `E-API-006` | the route exists, the method does not |
| 500 | `E-API-500` | an unexpected failure; the message is generic and the detail is in the server log |

## 5. Runs

`POST /workflows/{name}/runs` with `{"inputs": {...}, "version": 4}` (`version` optional: the latest
published one). The version must be published (409 otherwise). The inputs are validated as the
command line validates them, with defaults filled in; invalid inputs give 422 with one error per
input and nothing is stored. A valid request stores the run and answers **202** with

```json
{"run_id": "r-0001", "workflow": "quarterly_consolidation", "version": 4, "status": "CREATED"}
```

and the run is driven by the app on a pool of `max_runs` threads (default 4), each with the
`workers` of the scheduler (default 4). The ids and the clock come from the app's factory
(deterministic mode); the executable that starts the server passes `uuid` and `time`.

`GET /runs/{id}` is the report of the run: `status` (`CREATED`, `RUNNING`, `WAITING`, `FINISHED`),
`outcome` once finished, and per step the state, start, end and error (the `RunReport` of
`model/history.py`). `GET /runs?workflow=&status=` lists runs, newest first.

**A run is driven by one thread at a time.** The app keeps one lock per run id; a second
operation on a run that is being driven gets 409 `E-API-003`. The database is the second line of
defence: the pair (run, sequence number) of an event is unique, so two drivers could not both
write.

**Approval.** A run with a step in `WAITING` returns control to the app; nothing stays in memory.
`POST /runs/{id}/decisions` with `{"step": "approve", "decision": "approved"}` recovers the run
from its log (as `flowstat resume` does), delivers the answer and drives it again. It is checked,
in this order:

1. the run exists (404) and the step is `WAITING` in it (409);
2. `decision` is one of the decisions the step declares: the argument `decisions` of the step, or
   the default of the action (`approved`, `rejected`) (422 `E-API-005`);
3. the caller holds the title named by the argument `role` of the step, or is an admin (403
   `E-AUTH-002`). "A quarterly publication requires the approval of the chief statistician"
   (DR-04) is this check.

The answer is 202 with `{"run_id", "step", "decision", "by"}`: the checks are made before the
answer and the run is driven on in the background, like a new run. It is recorded with the name of
the user (`by`), not with the role string. The same checks 1 and 2
are used by the command line (`--decision`), which until now accepted two fixed words whatever the
step declared; the command line is the operator of the machine, so it asks for no title.

A stopped app leaves its unfinished runs as they are in the log. They are taken up by `flowstat
resume`; resuming every unfinished run at start is M8.

## 6. Workflows and the catalogue

`POST /validate` with `{"text": "..."}` checks a text without storing it and answers 200 with
`diagnostics` (errors) and `findings` (warnings and notes), each with `code`, `message`, `line`,
`column`: the same as `flowstat publish` prints. `POST /workflows/{name}/versions` stores a draft
(201, the new number, the same lists; a draft with errors is stored, as in the command line, and
cannot be published). `POST .../publish` publishes it (200), or answers 422 `E-API-004` with the
errors. A published version is never changed (A1). `PUT /catalogue/{list}` replaces a list of the
reference catalogue (`[{"code": "BIS", "name": "Bishkek"}]`); versions already published keep their
snapshot (A4).

## 7. Deployment

A deployment turns one published version into a service: a process or a container with the same
engine, a frozen copy of the version and its own write-ahead log. It is made with `POST
/deployments` `{"workflow": "...", "version": 4, "kind": "process" | "docker"}` (admin) and lives in
the table `deployments` of the registry (A6).

**The bundle** is a directory made by the packager (`deploy/package.py`):

```text
<root>/<service-id>/
  manifest.json    format, service_id, workflow, version, text_sha256, catalogue_sha256,
                   api_key_sha256, title_keys_sha256, created_at
  workflow.yaml    the published text, byte for byte
  catalogue.json   the catalogue snapshot of that version
  Dockerfile       rendered from the template (kind docker)
  app/             a copy of the flowstat package (kind docker)
  data/            input files of the actions (mounted into the container)
  service.db       the service's own write-ahead log, made at first start
```

The service (`python -m flowstat.api.service --bundle DIR --port P`) checks both hashes at start
and refuses to run if a file changed, compiles the text with the snapshot (the compiler is
deterministic, so the plan is the one the core ran), and serves:

| Route | Permission | Does |
| --- | --- | --- |
| `GET /health` | public | `{"status": "ok", "workflow": ..., "version": ...}` |
| `GET /schema` | `runs:read` | the inputs of the workflow (name, type, constraints) and the manifest hashes |
| `POST /runs` | `runs:start` | as `POST /workflows/{name}/runs`, for this one version (no `version` field) |
| `GET /runs`, `GET /runs/{id}` | `runs:read` | exactly as in section 5 |
| `POST /runs/{id}/decisions` | `runs:decide` | exactly as in section 5 |

The routes for runs are one function used by both apps, so they cannot drift. The service has no
route for workflows, the catalogue or deployments.

**Same results.** For the same version, the same inputs, the same input files and the same
answers to approvals, the report of a run on the service equals the report of a run in the core
in the final state of every step, its error code, its output and the outcome of the run; the times,
the run id and the name that answered an approval (`by`: a person in the core, `approver.<title>` in a
service) may differ. This is what US-30 is tested with (`tests/acceptance/test_us30.py`:
four scenarios, an approval, a rejection, missing forms, and a run below the quorum, in-process and
against a real process).

**Launchers.** `kind: process` starts `python -m flowstat.api.service` with `subprocess` on a free
port of 127.0.0.1 and waits for `/health` (a limit of 20 s, with the clock and the sleep injected).
`kind: docker` builds the image and starts a container with the bundle directory mounted (so
`data/` and `service.db` stay on the host) and the port published on 127.0.0.1;
if Docker cannot be reached the request fails with 409 and says so, and it does not fall back by
itself (the caller chooses the kind). The operating system is touched in `deploy/` and nowhere
else (NFR-07). `DELETE /deployments/{id}` stops the process or the container and marks the row
`stopped` (409 when it is not running). `POST /deployments` answers 201 with the row, `api_key` and `title_keys`,
the only time the keys are shown; 409 when the version is a draft, when there is no launcher for the
kind in this process, or when the service did not start (the row stays as `failed` with the end of
its log in `detail`). `python -m flowstat.serve` is the command that starts the core with both
launchers; the bundles are made under `--bundles`.

The registry row has: id, workflow, version, kind, endpoint, status (`starting`, `running`,
`failed`, `stopped`), the bundle path, the pid or container id, and the times. The events of the
service's runs reach the core by the outbox of M7 (US-31); the log of the service is already the
source of truth for them, and nothing in M6 assumes the core sees them.

## 8. What is not in M6

User management endpoints; passwords and a login page; TLS (terminate it in front of the server);
CORS (not needed, see section 9); rate limiting; token rotation; resuming runs when the app starts (M8);
the outbox and the ingest of events in the core (M7); the epoch fence and migration (M8); more than
one core process (the database is SQLite).

## 9. M7: the editor, the graph, completion and the events of services

### The pages

The core serves the editor at `/ui/` (static files, permission `public`: they hold no data and no
secret). The page calls the routes below with `Authorization: Bearer <token>`; the token is typed
into the page and kept in `sessionStorage` of that tab. The page and the API have the same origin, so
**CORS stays off**. The editor and the graph library are not in the repository: `scripts/fetch_ui.py`
puts the pinned packages (checked by SHA-512) in `src/flowstat/api/ui/vendor/`; without them the page
falls back to a text area and a list of diagnostics.

### New routes

| Route | Permission | Does |
| --- | --- | --- |
| `POST /graph` | `workflows:write` | body `{"text": ...}`; `{"diagnostics": [...], "graph": {...} or null}`; `graph` is null when the text has errors; stores nothing |
| `GET /workflows/{name}/versions/{n}/graph` | `workflows:read` | `{"graph": {...}}` of a stored version; 404 for an unknown version; 422 (`E-API-004`) when the stored text does not compile |
| `POST /complete` | `workflows:write` | body `{"text", "line", "column"}` (both from 1); `{"items": [{"label", "kind", "detail"}], "prefix", "from_column", "closed", "note": null or text}`; `from_column` is where the typed reference starts (replace from there to the cursor) and `closed` says whether the line already holds the closing brace (when not, the client writes it) |
| `POST /deployments/{id}/events` | `events:ingest` | body `{"run": {run_id, workflow, version, inputs, created_at}, "events": [...]}`; `{"run_id", "stored_through"}` |

Changed routes: `GET /runs` takes `?deployment=<id>` and every run says its `origin` (`null` for a
run of the core, the deployment id for a run shipped by a service); `GET /deployments` and
`GET /deployments/{id}` also give `runs` (how many runs the service has shipped) and
`last_event_at`. `POST /deployments` shows a third key, `ingest_key`, once.

### The graph

```json
{"nodes": [{"id": "load[BIS]", "base": "load", "action": "xlsx.read", "line": 14, "conditional": false,
            "goal": false, "group": "load", "layer": 1}],
 "edges": [{"source": "start", "target": "load[BIS]"}],
 "groups": [{"id": "load", "size": 9}]}
```

One node per step instance, one edge per entry of `needs` of an instance; `conditional` is true when
the step has a `when`; `goal` when it is a goal of the workflow; `group` names the base step of a
`foreach` (null for others); `layer` is the length of the longest path from a step without
predecessors.

### Completion

Inside `${` the answer holds, as whole references (`label` is what is written between `${` and `}`):
`inputs.<name>` for every input, first; then, in the order of the plan, the fields that the action
of a step declares, for the steps that `available_at` (C2) says can be read without a warning
(`approve.decision`); for a `foreach` step the list `step[*].field` when every instance is an
ancestor of the step (it holds the instances that succeeded, and the analysis warns only about an
index that needs more elements than the join guarantees), and `step[var].field` when the step is an
instance of a `foreach` with the same variable and that instance is guaranteed. A step whose action
declares no fields is offered as the bare name. The step is the one whose source lines contain the
cursor; outside the steps only the inputs are offered. In the `when` of a step its own condition is
not assumed (it is being written), so what the condition would justify appears once it is written.
When the text with the unfinished reference replaced by `true` does not compile, `items` is empty
and `note` names the first error; nothing is guessed. A cursor outside `${ }` gives no items and a
note. Columns count UTF-16 units as the editor does; a character outside the Basic Multilingual
Plane on the same line moves them by one.

### Events of a service

The service ships its events to the core in order and at least once:

- The core stores them with the rule of `RunStore.append`: an event already stored and identical is
  ignored, a different event with a used number or a gap refuses the whole batch and stores nothing
  (409 `E-API-003`, with `expected` in the message). The pair (run, sequence number) is the key.
- Authentication: the ingest key of the deployment (the third key, shown once). The key of another
  deployment, a key of a user, and no key are refused (403, 403, 401). A batch with a workflow or a
  version other than the deployment's is 422 `E-API-001`; a run id that belongs to the core is 409.
- At most 200 events in a batch. The service keeps a cursor per run (`outbox`, in its own database)
  that moves only after the core acknowledged; when the core answers with a gap, the cursor goes back
  to what the core has.
- The core reads a shipped run like its own: `GET /runs/{id}` gives the same report (the core holds the
  version that was deployed), `GET /runs?deployment=<id>` lists them. Shipped history may lag; the
  log of the service is the source of truth.
