# Decision journal

One entry per significant decision: date, what we chose, what we rejected, what we measured with.

## 2026-11-02 Repository structure and layering

- Chose: packages by layer (`model`, `dsl`, `solver`, `repo`, `compiler`, `analysis`, `engine`, `actions`, `recovery`, `migration`, `api`, `deploy`); dependencies point down only.
- Enforced by: `tests/architecture/test_layers.py`.
- Rejected: one flat package (a violation of the dependency direction would be invisible).

## 2026-11-02 Algorithms of C1-C6 are written by us

- Chose: our own lexer and recursive-descent parser for expressions, our own document shape validator, our own solver, scheduler and recovery. Third-party: only `ruamel.yaml` for reading YAML.
- Rejected: Lark (expression parsing), jsonschema (shape check), Z3 and sympy (solving), workflow frameworks.
- Enforced by: `tests/architecture/test_allowed_imports.py` and the allow-list in `reuse.md`.

## 2026-11-02 Feasible critical path

- Chose: priority of C3 from the longest chain of steps that can run in the same execution, decided by the solver of C6 (built in I4). I2 ships the plain longest path as the baseline.
- Measured with: benchmark FIFO against plain against feasible (to be done in I4).

## 2026-11-02 Two levels per C function, nine milestones

- Chose: every C function has two levels (a known algorithm with our transformation, then an algorithm built on its result); see `docs/algorithms/README.md`. I4 and I5 are split into two milestones each, and a hardening milestone M9 is added.
- Rejected: replacing C5 with write-conflict analysis (it would change DR-01, US-32/33 and the diagrams, and overlaps C2/C6). C5 stays; in M8 conformance is built before handover.
- Reason for the split: smaller milestones (about 20 to 30 SP) lower the risk of a late surprise.

## 2026-11-03 Working practice follows FleetSync

- Chose: the plan of the Project Proposal stays as it is; the way of working (goal, readiness audit, journal, research notes, review and retrospective per milestone, verification pass against the code) follows the FleetSync project. See `process.md`.

## 2026-11-05 Published versions are protected by the database

- Chose: SQLite triggers refuse UPDATE and DELETE on a published row; the store also refuses to publish twice. A published version keeps the catalogue snapshot it was compiled against, and its plan is always rebuilt from that snapshot.
- Rejected: protection only in code (a bug above the store could then change a published version without anyone noticing).
- Checked with: a test that runs raw SQL UPDATE and DELETE against a published row.

## 2026-11-05 The version number is part of the document

- Chose: publishing fails (E-VER-001) when the `version` in the text differs from the version number; the same for the workflow name (E-VER-002).
- Rejected: the service rewriting the designer's text. The text the designer wrote must be the text that is stored.
- Cost: after a publication the designer edits the `version` line of the next draft.

## 2026-11-05 A missing colon is reported where the key starts

- Found by: the acceptance test of US-03 (criterion: missing colon on line 14 gives an error on line 14). The YAML library notices the problem one line later.
- Chose: for this error the reader uses the position of the start of the key. Other YAML errors keep the position given by the library.

## 2026-11-05 Recount of story points

- Chose: keep 203 SP as the zero point of the burndown and show the added scope separately: parser and shape validator +6 SP (M1, delivered), feasible critical path +5 SP (M5, estimated). Planned total 214 SP.
- Rejected: changing the baseline (the burndown would then hide that the plan grew).
- Where recorded: `backlog.md`, section 3 of the Playbook and the planning paragraph of the Project Proposal.

## 2026-11-05 Process wording and documents brought in line

- The sentence in section 7 of the Project Proposal said ceremonies make no sense for one person, while section 11.5 keeps a minimal review and retrospective. Section 7 now refers to section 11.5.
- The Playbook names the nine regional offices (seven oblasts plus Bishkek and Osh) at its first mention; the catalogue keeps calling them regions.
- The copies of both documents in `docs/` are refreshed from the current text; the document properties carry only the author and the title.

## 2026-11-05 M2 starts: evaluation semantics before code

- Chose: strict, small semantics (booleans for `and`/`or`/`not`, no implicit conversions, `exists` as the only guard) written in `dsl-spec.md` 3.1 before the evaluator. A fan-in reads the instances that succeeded, in declaration order.
- Rejected: Python truthiness (a number in a `when` would silently work and hide a mistake), and `null` for a missing field (a typo in a field name would silently make a condition false).
- Checked with: the evaluator is compared with Python on random expressions (same value on every case in the common fragment) and with golden cases for every error code.

## 2026-11-05 Scheduler as a pure state machine

- Chose: the scheduler takes events (`step_done`, `step_failed`, `step_waiting`, `signal`, `cancel`) with the time of the event and returns commands (`Start`, `Cancel`). It starts no thread, sleeps never and reads no clock. The thread pool of production and the simulated clock of the tests are two drivers around the same core.
- Chose: `when` is evaluated when a step is decided (all dependencies terminal), not when a worker is free. The references of a condition point to ancestors only, so the value cannot depend on timing.
- Chose: the default failure policy stops the run; `continue` lets the failure travel through the joins. A stopped run ends as FAILED, a finished run whose goals do not hold ends as GOAL_NOT_REACHED.
- Deferred: `retry` (C4, M3) and a timeout of a waiting step (M3). The compiler accepts `retry`; the engine does not apply it yet, and `dsl-spec.md` 5.1 says so.
- Rejected: a recursive finish/decide cascade (a failure in a chain of 3,000 steps would exceed the recursion limit); the scheduler uses a queue.
- Checked with: 250 random workflows against an independent oracle (final states and outcome for 1, 2, 3 and 100 workers), runs of the same workflow under five different seeds and worker counts (identical states, outputs and errors), the invariants of the Proposal (no step starts before its dependencies are terminal, every step is terminal at the end, at most m running), and 4 acceptance tests of the stories.

## 2026-11-05 History of a run (A2)

- Chose: the history is the list of events and nothing else. The report of a run (state, start, end and error of each step) is a fold over the events (`model/history.py`), so no second copy of the state can disagree with the history.
- Chose: the table of events is append-only (triggers refuse UPDATE and DELETE), as published versions are immutable. A crash-recovery module (C4, M3) can replay it without doubting what was written.
- Chose: delivery is at least once. An event that is already stored, identical, is ignored; a different event with a used sequence number, or a gap in the numbers, refuses the whole batch and stores nothing (one transaction). This is the rule the outbox of the deployed service needs in M8, so it is cheaper to have it from the start.
- Chose: a run belongs to a published version; a draft cannot run (it may still change), and wrong inputs are refused before anything is stored.
- Chose: the run service does not know how steps are executed. It receives a driver (plan and inputs in, events out). The simulated driver is used in tests; US-12 adds the thread-pool driver with real actions.
- Rejected: storing the status of each step in a table next to the events (two sources of truth).

## 2026-11-05 Actions and the command line (B6)

- Chose: threads exist in one place only. Worker threads run the action of a step and put the outcome in a queue; the calling thread feeds outcomes to the scheduler one by one. The scheduler therefore needs no lock, and a run decided by threads is decided as in the simulation. Time comes from a `clock` argument.
- Chose: a cancelled step whose thread is still running is not interrupted; its late result is ignored. Killing a thread is unsafe in Python, and M3 handles side effects of an interrupted step through idempotence and compensation, not through interruption.
- Chose: the registry checks the arguments and the declared output schema of every call (E-ACT-009, E-ACT-004). The workflow text does not repeat the schemas (audit point 4 of M2).
- Chose: `db.write` stores rows under the id of the run and replaces on repetition, so a repeated call after a crash does not duplicate. Each call opens its own short connection to the target database, because several steps may write from several threads.
- Chose: `email.send` writes to an outbox table and sends nothing. Real mail is out of scope and a test must never contact anyone.
- Chose: paths in `xlsx.read` are relative to a data directory and may not leave it (E-ACT-007).
- Dropped: pandas. Summing by code over a few thousand rows is a dozen lines of standard library; the dependency would only add install time on both CI systems (`reuse.md` has the note).
- Chose: example workflow is version 1, so that `flowstat publish` works on an empty database.
- Left for M3: `retry` and its backoff, a timeout for a waiting step, resuming a run that was waiting when the process ended.

## 2026-11-05 Priority rules and the close of M2

- Measured: plain longest path against FIFO on 600 random DAGs (`research/priority-rules.md`). The longest path is within 0.4% of the lower bound; FIFO is up to 21.5% above it. Longest path was shorter in 534 cases, equal in 63, longer in 3, so it is a heuristic and not a guarantee, and the card says so.
- Chose: a `policy` argument of the scheduler (`longest_path` or `fifo`) only to have the baseline. It changes the order of starts and never the result, which a test checks.
- Not claimed: that the feasible critical path of M5 will win. The generated DAGs have no conditions that make a chain unreachable; M5 needs a generator with such branches and reports against this table.
- Closed: M2 review and retrospective, card C3 level 1, backlog and traceability. The tag `m2` is created by the user in the GitHub interface, as for `m1`.

## 2026-11-05 M3 audit

- Found: eleven gaps between the Proposal and the code of M2 (listed in `milestones/M3.md`). The two that would have cost most if found late: events were stored only after a run (a crash would lose the whole history), and the action interface had no idempotency flag, so recovery could not tell which steps may be run again.
- Chose: recovery folds the recorded states and does not replay decisions. Replaying the scheduler from the inputs would need the raw results of actions, which are not all in the log (a failed contract leaves no output), and a fold is also what the Proposal describes.
- Chose: the lost attempt of an idempotent step is run again with the same key, not a new one; with a new key the receiver could not recognise the repeat.
- Chose: an operator's retry of an UNCERTAIN step uses a new attempt number (the person accepts the risk of a repeat).
- Chose: no jitter in the backoff, so that a run and its tests are reproducible.
- Dropped: a timeout for a waiting step from M3 (no story, no criterion); written to M9. A warning for missing `compensate` moves to M5.
- Rejected: `httpx` for the HTTP action; the standard library is enough and one dependency fewer has to be installed on both CI systems.

## 2026-11-05 Retries (US-13)

- Chose: the scheduler returns the retry as a `Start` command with the next attempt number and the delay; the driver waits. Nothing in the scheduler sleeps or reads a clock, and the simulated driver turns the delay into virtual time.
- Chose: the delay is slept in the worker thread. The step keeps its slot during the delay (it is still RUNNING), which is what `dsl-spec.md` 5.3 says; the scheduler thread stays free for other events.
- Chose: `run_threaded` takes `sleep` as a required argument, not a default of `time.sleep`. A default would hide the one place where an engine reads real time; the command line passes `time.sleep`.
- Chose: the failed attempt is reported once, as `STEP_RETRY`, with the error that caused it; the step has no FAILED event until the last attempt fails. The report shows the number of attempts.
- Tested: every combination of `max` 0 to 3, both backoffs and 0 to 4 failures (40 cases) rather than random ones; the space is small enough to be exhaustive, and an exhaustive table is a better oracle than a random one here.
- Found: a database file made by M2 would not have the new columns of the event table (`CREATE TABLE IF NOT EXISTS` does not change an existing table). `open_database` now adds the missing columns, with a test on an old file.

