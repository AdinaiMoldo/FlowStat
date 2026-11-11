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
| `runs:migrate` | `POST /runs/{id}/migrate`, `POST /migrations/recover` | yes | no | yes |
| `statistics:read` | `GET /statistics` | yes | no | no |
| `handover:control` | `/migration/{run_id}/...` and `/keys...` of a deployed service | yes | no | no |
| `events:ingest` | `POST /deployments/{id}/events` (the ingest key of that deployment, section 9) | no | no | no |

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

A stopped app leaves its unfinished runs as they are in the log. **A process that starts takes up
the runs it owns** (US-36, M9): after the moves left in the journal are finished, every run of its
own database whose status is `CREATED` (stored, never started), `RUNNING` or `COMPENSATING` is
rebuilt from its history and driven on the pool, oldest first, with no command from anyone. A step
that was running is run again with its key when its action is idempotent and is `UNCERTAIN` when it
is not (dsl-spec 5.5); a finished step stays finished. A run that is `WAITING` (an approval, or a
step only a person can resolve) is not touched: its answer comes by the route. Only runs the store
owns are taken up: not one that is `MIGRATING` or `MIGRATED_OUT` (section 10), and not the core's
copy of a run that a service drives (its `origin` is that service, which takes up its own at its
own start). The lock of a run keeps an answer that arrives at the same moment from driving it twice
(409). `flowstat resume` stays for the command line, which has no server.

**A time limit on a waiting step** (US-38, M9; dsl-spec 5.6). A step with `timeout: T` that went to
`WAITING` fails with `E-RUN-009` when `T` seconds have passed since the recorded time of its wait.
Nothing stays in memory while a step waits, so the core and a service look at their own runs when
they start (after the runs left going are taken up) and then every `--expire-interval` seconds
(default 5, 0 looks only at start); a run is rebuilt only when its version has a step with a limit.
The failure is recorded at the time it is noticed and the `on_failure` of the step applies. An
answer to a step whose time has run out, and that the check has not yet failed, is refused with 409
`E-API-003` ("the answer came too late"), so an answer and a time limit are never both accepted.

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
service's runs reach the core by the outbox of M7 (US-31, section 9); the log of the service is the
source of truth for them, and the core's copy may lag.

**Rotating the keys of a service** (US-40, M9). Two of the four keys of a service can be replaced
without deploying it again, by an admin of the core: `POST /deployments/{id}/keys/control/rotate`
and `POST /deployments/{id}/keys/ingest/rotate` (the core makes the new key; it is not shown). Only
a service that is `running` and answers is rotated (409 otherwise).

- *The control key* (the core presents it). The core writes the new key to the row, tells the service
  its hash with the old key (`POST /keys/control/prepare`: the service now accepts both), then tells
  it with the new key to keep that key alone (`POST /keys/control/settle`), and the row keeps the
  new key. From then on the old key is refused (401).
- *The ingest key* (the service presents it; the core holds its hash). The core writes the new hash
  to the row and accepts both, gives the service the key with the control key
  (`POST /keys/ingest`: the service stores it, ships with it from its next send, and keeps it over a
  restart: the stored key wins over its environment), and then keeps only the new hash. From then
  on the old key is refused.

A stop of the core in the middle leaves a record in the row (`control_key_next`,
`ingest_key_next_sha256`). **After the next look, exactly one key works:** the core asks the
service, and if the service knows the new key the rotation is carried to its end, otherwise it is
taken back and the old key is the only one. The look is made when the core starts, before a new
rotation of the same deployment, and on request (`POST /deployments/{id}/keys/recover`, answers
`nothing`, `completed`, `taken back` or `away`). Until it is made both keys work, which is what lets
a send that is under way with the old key end. A service that does not answer is 503 `E-KEY-001`;
the record stays and the look decides later. The routes of the service (`GET /keys`,
`POST /keys/control/prepare`, `POST /keys/control/settle`, `POST /keys/ingest`) need `handover:control`,
and each can be called again with the same effect. Not rotated here: the key that starts runs and
the keys of the titles of a service (a new deployment is made for them).

**Health of a service** (US-37, M9). The status above is what the core did to the process; a
process that was killed, or a machine that restarted, left `running` in the row for ever. The core
therefore asks every `running` service for `GET /health` (twice, so one lost request is not
enough) when it starts and every `--probe-interval` seconds (default 10; 0 asks only at start). A
service that does not answer is listed with the status `unreachable` and `answering: false`, and
`answering_since` says since when; `detail` says what the core tried. When it answers again it is
`running` and `answering: true`. `answering` is `null` for a service that is not running or was not
asked yet. A row is written when the answer changes, not at every look, so a quiet registry
causes no writes. A service that was stopped on purpose, that failed, or that is still starting
is not asked. `unreachable` is not a stored word (the process was not stopped, so the database
keeps `running` and an old file needs no change): the core shows it, and treats it as not running
when a run is to be moved to it or called in it (section 10). An unreachable service can still be
stopped (`DELETE`), because a hung process is not a gone one. `POST /deployments` also shows the third
key, `ingest_key`; it is passed to the service by environment and its hash is in the registry row.

**Hosts of `http.call`** (US-39, M9). `python -m flowstat.serve --allow-host HOST` (repeat it, or
`FLOWSTAT_ALLOWED_HOSTS=a,b`) limits the hosts a step may call; the core passes the list to each
service it deploys (environment `FLOWSTAT_ALLOWED_HOSTS`), and a service reads it at its start. A
version whose constant URL names another host is refused when it is saved (E-TYPE-007), and a call
to another host fails before it is sent (E-ACT-011); dsl-spec 5.5 has the rules. A list changed in
the core reaches a service when it is deployed again.

## 8. What is not in M6 or M7

User management endpoints; passwords and a login page; TLS (terminate it in front of the server,
which matters more now that services send a key over the network); CORS (not needed, see section 9);
rate limiting; rotation of the tokens of users (not planned) and of the control and ingest keys of a service (done in US-40); resuming runs when the app starts (done in US-36); the
epoch fence and migration (done in M8); a health probe of the services from the registry (done in US-37); a
live refresh of the run list (US-42);
more than one core process (the database is SQLite).

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

Changed routes: `GET /runs` of the core takes `?deployment=<id>` and every run says its `origin`
(`null` for a run of the core, the deployment id for a run shipped by a service); the `GET /runs` of
a deployed service is as it was (a service has no origin to tell). `GET /deployments` and
`GET /deployments/{id}` also give `runs` (how many runs the service has shipped) and
`last_event_at` (the time of the last batch the core took from it, `null` before the first).
`POST /deployments` shows a third key, `ingest_key`, once. An error item may carry `expected` (an
integer), which only a refused batch with a gap uses.

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
  (409 `E-API-003`). For a gap the error item has `expected`, the number of the event the core needs
  next; for a different event it has none. The run and its events are stored in one transaction, so
  a refused first batch leaves no run behind. The pair (run, sequence number) is the key.
- Authentication: the ingest key of the deployment named in the path (the third key, shown once; only
  its SHA-256 is kept). No key, or a key that is nobody's, is 401. The key of another deployment, or
  of a deployment that does not exist, and the token of a user of any role are 403 `E-AUTH-002`: the
  permission `events:ingest` is held by no role. The ingest key opens no other route (401). A batch
  with a workflow or a version other than the deployment's is 422 `E-API-001`; so are an empty batch,
  one of more than 200 events, an unknown field and an unknown kind of event. A run id that belongs
  to the core, or to another deployment, is 409.
- At most 200 events in a batch. The service keeps a cursor per run (`outbox`, in its own database)
  that moves only after the core acknowledged; when the core answers with a gap, the cursor goes back
  to `expected - 1` (once per run in a round, so a core that keeps asking for what was just sent does
  not make a loop; that run is left out of the round).
- The service gets the address, the key and its own id in its environment (`FLOWSTAT_INGEST_URL`,
  `FLOWSTAT_INGEST_KEY`, `FLOWSTAT_DEPLOYMENT_ID`), never in the bundle or on a command line. A
  thread of the service makes a round when it starts and then every second; after a round that could
  not reach the core (no answer, 5xx, a refused key) it waits 1 s, 2 s, 4 s, up to 30 s, and a good
  round starts again at 1 s. When the service stops it makes one more round, so what the last run
  wrote is not left behind (a limit of `--stop-limit` seconds). A run that the core will not take
  (409 without `expected`, 422, 404) is logged and left out; the others go on.
- The core tells services its own address with `--ingest-url` (default `http://HOST:PORT`, with
  `0.0.0.0` read as `127.0.0.1`). A service in a container reaches the host as
  `host.docker.internal`: the launcher rewrites a loopback address and adds the `host-gateway`
  entry, and the core has to listen where the container can reach it (`--host 0.0.0.0`). Without an
  address (`ingest_url` unset) the keys are made and the service ships nothing.
- Everything a service ships is data from outside the core: the page puts it into the DOM as text and
  never as markup (a test ships a message holding an `<img onerror>` and finds no element).
- The core reads a shipped run like its own: `GET /runs/{id}` gives the same report (the core holds the
  version that was deployed), `GET /runs?deployment=<id>` lists them. Shipped history may lag; the
  log of the service is the source of truth.


## 10. M8: moving runs to a new version, and statistics

### Which runs can move (US-32)

`GET /workflows/{name}/migration?from=3&to=4` (`runs:read`). Both versions must be published (409
otherwise) and different (422). The answer is about the runs of version `from` that have not finished,
in the core and in the services (the copy the core holds), oldest first:

```json
{"workflow": "quarterly_consolidation", "from": 3, "to": 4,
 "runs": [{"run_id": "r-0007", "owner": "core", "status": "WAITING", "migratable": true,
           "reasons": [], "kept": ["load[BIS]", "approve"], "added": ["notify"],
           "pending": ["publish", "notify"], "dropped": []}]}
```

`owner` is `core` or a deployment id. `kept` are the steps of the history that exist in the new
version, `added` the steps of the new version that the old one did not have, `pending` the steps of
the new version that the history has not reached (the added steps, and the old ones that had not
started; the instances of a region that was added), `dropped` the steps of the old version that are
not in the new one and were never touched. When `migratable` is false `reasons` holds *all* the reasons, each
`{"code", "step", "message"}`:

| Code | Reason |
| --- | --- |
| `E-MIG-001` NEVER_MIGRATE | the run is compensating or has an uncertain step (C4 owns it); it is the only reason given for such a run, because the others would not be about a run that can be changed |
| `E-MIG-002` RUNNING_STEP | a call is in flight; ask again when it has finished |
| `E-MIG-003` REMOVED_TOUCHED_STEP | the step has events and the new version does not have it (a rename is a removal plus a new step) |
| `E-MIG-004` CHANGED_TOUCHED_STEP | the action, `with`, `ensures`, `compensate` or loop binding of a step with events differ (compared as meaning, not as text), or the `on_failure` of a step that failed, whose policy was already applied to the rest of the run |
| `E-MIG-005` UPSTREAM_CHANGED | a step that started could not start under the join rule of the new version, given what its predecessors did |
| `E-MIG-006` GUARD_CONTRADICTS_HISTORY | a skipped step whose new guard is true on the recorded values, or a started step whose new guard is false |
| `E-MIG-007` OUTPUT_INCOMPATIBLE | a recorded output lacks a field a step that has not started reads, or is of another type than the argument it goes to |
| `E-MIG-008` INPUTS_REJECTED | the new version refuses an input of the run (a new required input, another type or pattern); `step` holds the input name. New defaults are added and an input the new version no longer declares is dropped |

The check is made against the compiled plans of the two stored versions, each with the catalogue
snapshot it froze. It reads the history and changes nothing. A change of `retry` is allowed, and of
`on_failure` too, except for a step that failed; a step that has not started may change in every way.

### Moving a run (US-33)

`POST /runs/{id}/migrate` (`runs:migrate`), body `{"to": 4, "owner": "core" | "<deployment id>"}`.
`owner` is where the run goes: the core, or a deployment that is `running` and serves version `to`
(409 otherwise). The check of the previous section is made first; a run that is not migratable is 409
`E-API-003` with the reasons as error items. Then the handover:

0. **Journal.** The core writes the intent (the plan: run, both owners, epoch, version, inputs,
   `last_seq`) before it calls anyone, and removes it when the move is complete.
1. **Fence** at the current owner, under the run lock: `OWNED(e)` becomes `MIGRATING(e+1)` in one
   transaction, only if the epoch is `e` and the history still ends at the `last_seq` of the check
   (otherwise the check was about another history) and no step is being driven. From now on every
   command for the run at that owner is refused with 409 `E-MIG-010`, whose message names the new
   owner. A repeated fence for the same plan answers the same.
2. **Adopt** at the new owner, idempotent by (run, epoch): the row, the events and `OWNED(e+1)` as
   exported by the old owner, then the event `RUN_MIGRATED` (`attempt` = the epoch, `message` = the
   old owner), in one transaction. The new owner restores the run with the new plan; the new steps
   are `PENDING` and start when their turn comes.
3. **Release** at the old owner: `MIGRATING` becomes `MIGRATED_OUT`; repeatable. The core then
   updates its own copy of the run (`origin`, version and inputs) and ends the journal entry.

A move inside one store (the core to the core, to a newer version) is a single transaction that
changes the version and the inputs of the run and bumps the epoch; there is nothing to fence.

The answer is `{"run_id", "from", "to", "owner", "epoch", "status"}`. Ownership is a table in every
store that can own a run, not an event: the log of the old owner gets no event, so the log keeps its
key (run, sequence number) and the numbers continue at the new owner. A crash between two of the
steps leaves one of three states, and `POST /migrations/recover` (`runs:migrate`; also run when the
core starts) takes each such run forward: no import yet, repeat the import; imported, release. It
never goes back; it reads the two owners, not the journal, to decide what is left. An approval that arrives for a run that is `MIGRATING` or `MIGRATED_OUT` is refused,
not queued, and is sent again to the new owner.

Between the core and a service the calls are the routes of the service under `/migration/{run_id}/`:
`GET ownership`, `GET export`, `POST fence`, `POST adopt` and `POST release`. They need the
permission `handover:control`, which only an admin holds, and the only admin of a service is the user
`control`: the keys that start runs and answer approvals are operators and are refused (403). The
core presents the *control key* of the deployment. It is a fourth key made with the others; its hash
is in the manifest (`control_key_sha256`) and in the service's database, and the core keeps the key
itself in the registry row because it has to present it. It is never shown, never in a listing and
never in a bundle. Stated limit: a secret at rest in the core's database.

Errors of a move: 404 `E-API-002` (no such run or version); 422 `E-API-001` (the run is on that
version already); 409 `E-API-003` (a version is a draft, the target is not a running deployment of
the version, a call is in flight, the history changed since the check, another move of the run is in
flight, or the run cannot move: the reasons follow as further error items, each with its code and
step); 409 `E-MIG-010` (the store does not own the run: it is moving or has moved, the message names
the owner); 503 `E-MIG-011` (an owner does not answer; the move is in the journal and
`POST /migrations/recover` takes it forward). The same `E-MIG-010` answers a decision or a `resume`
sent to a store that does not own the run.

### Statistics (US-34)

`GET /statistics` (`statistics:read`, admin). For every workflow that has runs, from the stored events
(including the copy of the services' runs, which may lag):

```json
{"workflows": [{"workflow": "quarterly_consolidation", "runs": 10, "running": 0, "finished": 10,
                "succeeded": 7, "not_reached": 1, "cancelled": 0, "failed": 2,
                "failure_rate": 0.2, "average_duration": 41.5,
                "versions": {"3": {"runs": 4, "failed": 1}}}]}
```

A *failure* is an outcome of `FAILED`, `COMPENSATED` or `COMPENSATION_FAILED`; `GOAL_NOT_REACHED` is a
result and is counted apart (DR-02); a `CANCELLED` run is finished, counts in `cancelled` and is not a failure.
`running` is every run that has not finished (created, running or waiting). `failure_rate` is `failed / finished` (null when nothing has
finished), `average_duration` the mean of `RUN_FINISHED.at - RUN_STARTED.at` over finished runs in
the unit of the engine's clock (seconds), null when nothing has finished.
