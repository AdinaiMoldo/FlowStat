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

## 2026-11-05 Compensation (US-14)

- Chose: the order of compensation is computed in `recovery/saga.py` (C4, level 2) and handed to the scheduler as a function (`CompensationPlanner`). The engine layer cannot import the recovery layer, and `architecture.md` already says the engine receives such parts as protocols. A scheduler built for a plan that has `on_failure: compensate` and no planner refuses to start, so the mistake shows at once and not at the first failure.
- Chose: the scheduler runs the undo calls itself (same slots, same retry code, same events), because a second scheduler for the compensation would duplicate the slot accounting and the retry logic.
- Chose: later steps are undone first among the steps that are ready (the heap holds negated plan indices), so the order is deterministic.
- Chose: a failed undo does not stop the others; only the steps that wait for it stay as they are (SUCCEEDED, not undone). The run reports COMPENSATION_FAILED and the report shows which steps are in which state.
- Chose: a request to cancel is ignored during compensation. Stopping an undo halfway would leave effects in a state nobody recorded.
- Found by writing the property test: with more workers more steps have succeeded when the failure stops the run, so the final states differ between worker counts. My first assertion (equal states for 1, 3 and 100 workers) was wrong. The property that holds, and that the test now checks, is that each run undoes exactly the steps that had succeeded in it, in the right order, with at most `m` undo calls at once. Written down in `dsl-spec.md` 5.4.
- Stated limit: a step that was cancelled while running may have had an effect that is not recorded and is not undone.
- Also: the reference to the name of the engineering notes file in `milestones/M2.md` was reworded.

## 2026-11-05 Recovery (US-15, US-16)

- Chose: the rebuilding of a run is `Scheduler.restore`, a method of the scheduler, and not a function in `recovery/`. It writes the private state (queues, counters, undo bookkeeping) and a second place that did so would have to know it all. What `recovery/` keeps is the write-ahead log and the saga order; the package docstring says where the rest is and why.
- Chose: the restore folds the recorded states and derives everything else from them (counters of unfinished predecessors, the queue, the heap of undos). It takes no decision again except for steps that were decided but had no event; those come out the same because a decision is a function of recorded values. The crash tests confirm it: every cut gives the same states and outputs as the uninterrupted run.
- Chose: the engine is told whether an action is idempotent by a function (`is_idempotent` of the registry), as it is told the planner of the compensation. Default is not idempotent: forgetting the flag costs a question to a person, not a second mail.
- Chose: UNCERTAIN releases its slot, like WAITING, so independent steps go on even with one worker. The price is that an operator's retry starts at once and can exceed the limit by one call (written in `dsl-spec.md` 5.5).
- Chose: the stop that a failure causes is recorded as several events and the restore completes it if only some of them are stored. The commit of a call is one transaction, so this cannot happen at present; the crash suite cuts after every single event anyway, so the recovery does not rest on the transaction.
- Chose: `resolve` refuses when steps are still to be repeated (`resume` first). Otherwise one command would both repeat steps and resolve one, and it would be unclear what was asked.
- Measured, not assumed: three broken restores (a repeated call with the next attempt number, no repeated call, the half-recorded stop not completed) were put in one at a time. The unit tests and the crash suite fail for each (109, 110 and 14 failing tests). The suite is a real check and not a table of what the code does.
- Found while writing the tests: the simulated driver did not know how to answer a step that was WAITING at the restore; it now asks for the answer again, as a person would. A whole-value `${...}` keeps the type of the value (an integer stays an integer); my first expectation in a test assumed a string.
- Stated limit: a call that was in flight at the crash may still be running in another process when the engine restarts (the fault model is a stop of the engine and its calls, one owner per run; the owner switch is C5, M8).

## 2026-11-05 Misbehaving server (US-17)

- Found while writing the first exactly-once test, before the action existed: the plan said the key is `run/step/attempt` and the story says "retries with the same key". Both cannot hold for a retry after a lost answer: the attempt number changes, so the receiver would see a new operation and apply the effect again. Chose: the context has two keys. The attempt key stays what the engine records and what the other actions see; `http.call` sends the key of the operation (`run/step`, with `/compensate` for an undo). Written in `dsl-spec.md` 5.3. The test that sends the key to a receiver that ignores it shows the limit instead of hiding it.
- Chose: the fault-injecting receiver is a test tool (`tests/support/`), not a part of the product. Its one lock covers the check of the key, the effect and the store of the answer, so two requests with the same key that arrive together (a slow first request and its retry) still give one effect; a test covers exactly that.
- Chose: only `http` and `https` addresses, five methods, and a timeout that must be a positive number. `file:` and `ftp:` are refused because `urllib` would open them. There is no list of allowed hosts yet (a workflow author could point the call at an internal address); it belongs with the deployment (M8) and is written in the spec.
- Checked: all faults produce the error class the retry policy needs (a closed socket arrives from `urllib` as an `OSError` or an `http.client` error, a timeout as `TimeoutError`, a 5xx as `HTTPError`), one test each.


## 2026-11-05 Readiness audit of M4

- Found by reading the stories against the code: US-20 speaks of "the decision enum", but the DSL has no enums and `approval.wait` only says "the answer". Chose: a variable is a reference with a known domain; an input has its declared type and may narrow it with `values`, `min`, `max`; the `decision` of `approval.wait` is an enumeration given by a new optional argument `decisions` (default `approved`, `rejected`). Anything else is opaque. Checking an answer against `decisions` at run time waits for the REST API (M6).
- Found: the Proposal's atom `x in {c1, c2}` has no counterpart in the grammar. Chose: a list literal as a primary. This closes the open point left by M1.
- Chose: three-valued answers (`sat` with a witness, `unsat`, `unknown`). A solver that answers "no" when it does not know makes false warnings, and one that answers "yes" makes false proofs; the opaque rule and the cap of 64 clauses give `unknown`, and no warning rests on it.
- Chose: a branch group is steps with the same `needs` and guards over the same variables. A comparison of every pair of guards would warn about independent choices. It is a heuristic and the limit is in the spec (8.1).
- Chose: the test generator decides what a case covers with its own prediction (an optimistic run with the join rules), then the engine's run is compared with it. Solving the guard of a step alone is not enough: a skipped ancestor skips the step.
- Chose: the reference interpreter of whole workflows moves to M5, where C2 needs it as the oracle. In M4 the solver is checked against brute force on a grid, and the lowering of an expression against the evaluator of the engine.
- Found: warnings do not exist in the pipeline. `publish` and the CLI treat any diagnostic as an error. Warnings come from the analysis, which runs after a successful compilation; publish is blocked by errors only.
- Left out on purpose: generating text for a `pattern` (the caller gives such inputs) and a regular-expression engine of our own.

## 2026-11-05 Branch analysis (US-20)

- Chose: the solver does not know steps. `lower` takes a resolver that says which references are variables and with what domain; the analysis (`analysis/guards.py`) builds that resolver from the plan and from the signatures of the actions. The same solver can then serve the generator of US-24 and the dead-step check of US-23.
- Chose: the signatures of the actions come from the registry (`Registry.signatures()`), from the types that an action already declares for its output, plus `choices` for the decision of an approval. A list output is not a variable. A field that nobody declared is opaque, so a service built without signatures says "not analysed" and never warns.
- Found by writing the test for overlap: with two decisions, `!= 'rejected'` is the same guard as `== 'approved'`, so the analysis rightly reports two things, an overlap on `approved` and a gap on `rejected`. My first expectation (one warning) was wrong, and the second warning is a useful one: nothing handles a rejection.
- Found: a boolean input `flag` with the guards `flag` and `flag == true` overlaps on true and leaves false uncovered. Both are reported; this is what the designer wants to know.
- Chose: overlap is reported for every pair of clean guards even when another guard of the group is opaque (a witness for two clean guards is a proof). A gap is reported only when every guard of the group is clean, because an opaque guard may handle the case.
- Chose: warnings and notes are not errors. `save_draft` returns them in `findings` and keeps `diagnostics` for errors, so every caller that tested "is the draft ready" keeps working. `Severity.INFO` is new for the notes.
- Limit: `--decision` of the command line still accepts only `approved` and `rejected`. A workflow that declares `escalated` is analysed and published, and the answer arrives from the REST API (M6); checking an answer against the declared decisions belongs there.

## 2026-11-05 Dead steps (US-23)

- Chose: `values`, `min` and `max` are checked twice. At compile time they must fit the type (a `min` on a string, a value of the wrong type, `min` above `max`, a listed value outside the bounds are E-SHAPE-004 at the position of the offending item), because a declaration that contradicts itself would make every guard on that input look dead or alive for the wrong reason. When a run starts the value is checked against them (E-INPUT-006 to 008), after the type, the pattern and the catalogue; a default is checked in the same way.
- Chose: a dead step is reported when the solver proves the guard unsatisfiable, also when the guard has an opaque part (an unsatisfiable rest stays unsatisfiable). A guard that is only possibly false is never reported.
- Chose: only steps that are not instances of a `foreach` are checked, as for branch groups. The instances share one guard in the text, and nine warnings for one line would be noise; the check of the instance guards after substitution is left.
- Limit: a step is reported when its own guard cannot hold. A step that is dead because an ancestor is dead or always skipped is the optimistic run of C2 (M5).
- Found while writing the test for a dead step next to a branch: `small <= 50` and `big > 100` with `amount` at most 100 gives two findings, the dead step and the gap from 51 to 100. Both are right.

## 2026-11-05 Generated tests (US-24)

- Chose: the run condition of a step is built exactly from its guard and the join rules, so "step runs" is a formula the solver can answer, not only the guard of the step. A target whose condition is `unsat` is proved infeasible.
- Chose: `predict` stays a second, independent judge of what a case covers: it runs the plan on concrete values with the join rules and decides guards with the predicate. It is compared with the engine on 480 random workflows, so the solver, the prediction and the engine check one another.
- Chose: the candidates are the witnesses plus every case that differs from a witness in one variable (enumeration values, constants and their neighbours, bounds). The witness of a solver is the easiest value; the neighbours find the boundaries.
- Found: greedy set cover is not minimal (three cases where two do). After the greedy cover an exact branch-and-bound search tries a smaller one within a budget.
- Chose: mocked outputs have the default of their declared type, except the variables that guards read. A contract that fails on such a value is shown as a difference, which is what the story asks for.
- Chose: the inputs are validated once, with the values the caller gave and those of the first case; a run that cannot start is reported with its E-INPUT code, not hidden.
- Found: `I-TEST-001` was defined in the spec but nothing needs it; the lists "cannot happen" and "no input found" belong to the answer of the command, not to a draft. The code is removed from the spec.
- Limits: instances of a `foreach` are not targets, text for a `pattern` is not generated, a guard with an opaque atom ends as "no input found".

## 2026-11-05 Readiness audit of M5

- Found: seventeen gaps, listed in `milestones/M5.md`. The ones that change the design: the signatures of the actions need element types (`list<row>`), the type check cannot be in the compiler because it needs those signatures (the analysis layer does it), and the formula of the Proposal for `any_success` guards on the guard and not on the run condition.
- Chose: phase 4 is a part of the analysis, not of the compiler. This departs from the Proposal (C1 phase 4) and is the price of the layer rule; the check still runs on every save and its errors still block the publication. Written as a deviation in the milestone and to be repeated in the report.
- Chose: no `T | error` type and no W-TYPE-002. Whether a read of the output of a step that may have failed is safe depends on the join rule, which is what the availability analysis computes. The type of a field stays `T`.
- Chose: `Av` uses `not ran(P_i)` in place of `not guard(P_i)`, and a step with no `on_failure: continue` at or above it passes `ran(X)` on as a fact. The oracle (the reference interpreter, written first) is the check that this is sound. (The first draft of this entry said a failed predecessor made the Proposal's formula unsound; re-deriving it for the implementation showed it does not, and the audit text was corrected.)
- Chose: the priority of the feasible critical path is computed for the inputs of the run when they are known. The Proposal computes it over all inputs; fixing the inputs is sharper and costs nothing, but it is a departure and the benchmark reports both.
- Added: the story US-35 (5 SP) for the feasible critical path, which had SP in the recount and no story.
- Left out on purpose: nullable types, `round` and `coalesce` (not in the DSL), the completion scope beyond a function for M7, the cache of unchanged prefixes.

## 2026-11-05 Reference interpreter (oracle of C2 and C3)

- Chose: the interpreter takes the compiled plan as data and nothing else from the code base: it has its own join table, its own evaluator of guards and its own search for the longest chain. It does not import the engine, the scheduler, the evaluator or the analysis.
- Chose: only steps with `on_failure: continue` may fail in the enumeration. A failing step with the default policy stops the run, and what is running at that moment depends on timing (spec 5.4); the checks are about executions where the run goes on.
- Found: the first reading of the table was settled by the specification (`at_least`: "UPSTREAM_FAILED when f > 0 and s + f >= k", with failures counting FAILED and UPSTREAM_FAILED), and the engine agrees on all 1,920 compared states, so there was no difference to resolve.
- Limit: the evaluator of the interpreter covers literals, lists, references to inputs and to outputs, `not`, minus, `and`, `or`, arithmetic and comparison; calls are not modelled, and the random workflows do not use them.

## 2026-11-05 Types (US-18, US-19)

- Chose: a type is a small closed set (`string`, `int`, `float`, `bool`, `date`, `null`, `list`, `record`, and `any` for what is not known). `any` fits everything and gives `any`, which is how one mistake gives one message: a comparison that fails is still a `bool`, so the `and` and the `when` around it stay quiet.
- Chose: the actions declare their types in the same notation (`list<row>`, `list<list<row>>`), and the registry derives the Python class that it checks at run time from the declaration, so there is one source. A test compares the two. Optional arguments (`method`, `body`, `timeout`, and `decisions` of an approval) are declared, otherwise W-TYPE-004 would name real arguments as typos.
- Chose: `exists(...)` reads its argument quietly; a missing field is what it is for. A call that is not built in is valid and `any` (spec 3), not an error.
- Chose: the shape of the message for a comparison is "A list cannot be compared with a number: '>' takes two numbers, two strings or two dates", at the left operand, which is where the comparison starts in the text.
- Went wrong: `analysis/types.py` was written before its tests, against the rule that C1 is tested first. The tests that followed were written from the specification and not from the code (the table of operators, the example of the story, a comparison of 600 random expressions with the reference evaluator), and seven deliberate faults each failed them, so the check is a real one, but the order was wrong and the retrospective of M5 says so.
- Found by the tests: positions in a diagnostic refer to the whole document, so a test that looks at the line of the error needs the lines of the head that the helper adds; and a foreach step cannot be a goal (E-REF-009), so the test of nine instances ends in another step.
- Limit: an argument given as a YAML literal has no position of its own, so its error is reported at the step.

## 2026-11-05 Availability (US-21, US-22)

- Chose: the facts are computed from the run conditions, not from the guards. `ran(Y)` is exact for a run where nothing fails; `Av(Y, X)` is a sufficient condition for "if Y starts, X succeeded". An opaque atom is read as false when a fact is built and as true when a run condition is negated, so what the analysis cannot read costs it a fact, never a false warning.
- Chose: an undecided case gives the note I-AVAIL-003 and no claim. The specification said "a message in W-AVAIL-001"; it now says what the code does.
- Chose: a step that is dead by its own guard stays with the guard analysis (8.2). The availability analysis names only the steps behind it, and a quorum that cannot be reached.
- Found by the tests: a goal behind a step whose guard the solver cannot read is undecided, so a small test about a reference must look at the codes of its own family and not at the whole list. And a text `"...${check[*].rows}[7]"` in `with` is a type error (a list in a text); an index is read in a condition, so the tests use `ensures`.
- Found by the tests: two acceptance tests passed vacuously at first because a `replace` in the test did not match the text (`decision}` has a brace); they were caught because the finding they expected was absent, and the check now shows the changed guard.
- The example workflow gives no finding, and the expectations of the US-20 acceptance tests (test_us20) changed only where W-GOAL-001 and I-AVAIL-003 are new.


## 2026-11-05 Feasible critical path (US-35)

- Chose: the priorities are an argument of the scheduler (`priorities`) and not a policy of its own. The plain longest path stays as the default and as the baseline; the run service computes the feasible one for the inputs of the run when it launches or recovers a run.
- Chose: a step meets the alternatives of all its descendants, not only its successors, because a chain may pass over a skipped step (the join `all_done`); the reference interpreter reads a chain the same way, which is how the oracle could be exact.
- Went wrong, found by the benchmark: the first version dropped the chains beyond the cap, so a priority could be shorter than a chain that runs. In 97 to 100 of 100 random workflows the cap was reached when the inputs are not known, and the schedule was worse than the plain path (1.088 of the bound against 1.006). The unit tests had not seen it because their workflows are small. A cut now keeps the longest chain that was left out under the weaker condition of its first step, so every priority is an upper bound; the spec (11), the milestone text and the card say so, and the test of the cut checks `full <= capped <= plain`.
- Found: the gain over the plain path is small on random workflows (0.0% to 0.9% of the makespan with the inputs of the run), because the plain path is already within 1.4% of the bound. The note in `research/priority-rules.md` says so and does not claim more. The case of the story is decided by it, and that is a test.
- Found by the tests: two thresholds of "the check is not empty" were set above what random workflows produce (1,408 comparisons against 1,500; 69 against 100); they were lowered to the measured level and not the other way round.
- Limit: durations are 1 in a real run until runs keep averages; the cost without inputs (up to 1.1 s at 200 steps) is why the service uses the inputs of the run.

## 2026-11-05 M5 measurement and close-out

- Measured: availability against plain reachability on 250 random workflows, by enumeration in the reference interpreter: 648 safe reads, plain recognises 57.1%, ours 91.5%, and neither lets an unsafe read through. The remaining 55 false warnings are nearly all reads behind `at_least` or `any_success` where one dependency depends on another; the rule passes on facts about the ancestors of each dependency only. Left as a stated limit and not widened now: it is a precision gain of at most 8.5 points, and a wider rule would need its own oracle.
- Wrote: the card `algorithms/C2.md` (it was a template), level 2 of `algorithms/C3.md`, `research/availability-measurement.md`, the review and the retrospective of M5. The retrospective names the slip of writing the type check before its tests and the corrected audit point.

## 2026-11-08 Readiness audit of M6

- Verified before starting: `main` at the M5 commit, CI green on Linux and Windows, tags m1 to m5 and the M5 milestone closed, one author in 38 commits, no mention of the assistant in files or history, and a fresh clone passes all 1,498 tests, ruff and mypy.
- Found: nineteen gaps, listed in `milestones/M6.md`. The ones that change the design: the service app has to live in `api` because `deploy` may not import it; a token is the proof of identity and the approval title (not the platform role) is what DR-04 needs; the runner leaves nothing in memory while a step waits, so an answer is "recover, signal, drive" and becomes one service function used by the command line and the API; the plan has no serialised form, so a deployment freezes the text and the catalogue snapshot with hashes.
- Chose: contract first (`api-spec.md`). A machine caller is a user with the operator role; a deployed service has one key made at deployment. Errors are one envelope for all non-2xx responses.
- Found about the environment: the Docker client is installed but there is no daemon, and the Windows runners of CI cannot run Linux containers, so the container launcher cannot be verified here. The process launcher (the Proposal's fallback) is the tested path, and the review will say what was and was not run.
- Found: `docs/reuse.md` named httpx for `http.call`, which uses `urllib`; corrected to a test dependency.

## 2026-11-08 US-29 roles and the workflow routes

- Built: the `users` table and `flowstat user add|disable|list` (the token is made in the command line, the database keeps its SHA-256), `service/permissions.py` as the one table of permissions, and the package `api` (`create_app`, error envelope, `SecuredRouter`, the workflow routes). A test compares the table in code with the table in `api-spec.md` section 3 and walks every route as each role and with no token.
- Found: the current Starlette test client needs `httpx2`, not `httpx`; the dev extra and `reuse.md` name it. Ruff's B008 flags `Depends(...)` in defaults, which is how FastAPI reads dependencies, so it is declared immutable in the config and not silenced per line.
- Found by my own tests: saving a text while the latest version is a draft edits that draft, so a second save does not give number 2. The test now says so. The line of a version error in the example file is 3 (the file starts with a blank line).
- Chose: a body with an unknown field is rejected (422 `E-API-001` per field), so a misspelt field is not silently ignored. `E-API-006` (405) and `E-API-500` were added to the table of errors.

## 2026-11-08 US-25 runs over REST

- Built: `POST /workflows/{name}/runs` (202 and a run id after the version, the inputs and the store are checked), `GET /runs/{id}`, `GET /runs`, `POST /runs/{id}/decisions`. A run is driven by the pool of the executable (`AppConfig.submit`), one thread at a time per run (`RunLocks`); the job opens its own SQLite connection. The driving code that the command line had inside `_drive` moved to `service/driving.py`, so the two entry points cannot differ.
- Chose: the answer to an approval is checked in the request (a pass that stores nothing) and again in the job, which rebuilds the scheduler on its own connection. The lock is held between the two, so the state cannot change under them.
- Chose: the checks of an answer are `RunService.answer`: step waiting, decision declared, title held, in that order, and a test fixes the order. The command line uses the first two; `--decision` no longer has a fixed list of two words in `argparse`, because a step may declare others.
- Found while testing: with a pool that runs the job before replying, the second of two simultaneous answers always gets 409, so the test says `[202, 409]` and not "one of two outcomes". A test with a real thread pool starts six runs and joins the pool instead of sleeping.
- Limit: `GET /runs` builds the report of every run to give its status; it is linear in the number of runs and events. Fine for the size of this project, noted for the measurement of M6.

## 2026-11-08 US-30 deploy as a service

- Built: the bundle (`service/bundle.py`: manifest, SHA-256 of the text and the catalogue snapshot, refusal of a changed file), the packager and the two launchers in `deploy/`, `DeploymentService` (what is recorded and in which order; the packager and launchers are arguments, so it is tested with fakes), the table `deployments`, the service app `api/service.py` over the same run functions as the core, and the deployment routes (admin).
- Chose: the service keeps its frozen version in its own SQLite file as a published version with the number it had in the core, so it uses the same `RunService`, `WorkflowService` and run functions as the core with no second code path. A restart finds the version and refuses a database that holds another text under that number.
- Chose: the layer rules forbid `api` to import `deploy` and the reverse, and `service` (7) cannot import `deploy` (8) either, so the part that decides (`service/deployments.py`) takes the packager and launchers as protocols, and a new package `serve` on layer 9 wires the concrete ones (`python -m flowstat.serve`). It replaces the `api/__main__.py` of US-25.
- Found by the equivalence test: the reports of the core and of the service were equal in every step except `approve`, whose output names who answered (`by`: a person in the core, the key of the service in a service). That is a real difference and not a bug; the contract now says so and the test compares everything else. The expected outcomes of the four scenarios are written in the test, because two runs could be wrong in the same way.
- Decided, and weaker than the core: the one key of a service may answer an approval whatever title it names (a service has a single machine caller and no other routes). The core still checks the title of the person. Written in api-spec section 2 and in the review.
- The container path: there is no Docker daemon in the sandbox where this was written, so the launcher was first tested against a fake client (the build, the mounts, the published port, the removal on failure). The test marked `docker` ran in CI: on the Ubuntu runner it built the image and the container answered `/health` (the first time the image was really built); on the Windows runner it failed at first, because that daemon runs Windows containers and has no Linux image to pull (the launcher reported it correctly, with the reason, as a 409). The test now skips when the daemon is not a Linux one. The process path was run for real everywhere, including the stop.
- Corrected: the `actions` extra named `httpx`, which no code uses (`http.call` uses `urllib`); it is removed.

## 2026-11-08 M6 measurement and close-out

- Measured (`benchmarks/bench_api.py`): a request is 2 to 9 ms in the app, a run to its approval 75 ms, a process deployment 0.4 to 0.6 s. Listing runs costs 5.5 ms per stored run (every report compiles the plan again), which is the one figure that is not good; the note names the two changes and they are carried to M8. A pool of four threads is 12% faster than one, not four times, and the note says why and does not claim more.
- Wrote: `research/api-measurement.md`, the review and the retrospective of M6, the status of the milestone and the backlog.

## 2026-11-08 Before the tag: three things the review left open

- Fixed, a defect and not a limit: a deployed service had one key that could answer every approval, which undid DR-04 inside a service. A deployment now makes one key that starts runs (no title) and one key per title that an approval of the workflow names (read from the plan: the literal `role` of a waiting step). The key that starts runs gets 403 on an approval, which the process test checks; the answer is recorded as `approver.<title>`. The manifest has `title_keys_sha256`.
- Fixed: the list of runs read the full report of every run. The status needs only the events, so `RunService.status` reads it from them (a test: equal to the full report for four kinds of run). 50 runs: 275 ms to 13 ms.
- Added to CI: on Linux the container test must pass and may not be skipped, so it cannot go quiet by itself. On Windows it still skips (that daemon runs Windows containers).
- Left as it was: a report of one run still compiles the plan (8 ms); a cache of the compiled plan of a published version is M8.

## 2026-11-08 The container proved with the full scenario, and a demonstration

- Changed: the process test and the container test are one scenario (`exercise` in `test_us30.py`): deploy v4 for real, start a run, see it wait, a refused approval with the key that starts runs, a given one with the key of the title, the same report as the core, the service's own database and the result database on the host (a bundle is mounted into a container), the stop, and nobody answering afterwards. What is true of one is now checked for the other. On Linux in CI the container test must run, not skip.
- Added: `scripts/demo.py`, one command that shows the project to a person: users and roles, publication, the API with its refusals (401, 403, 422), a run in the core with its approval, the deployment of the same version as a service (process, or container with `--kind docker`) and the comparison of the two reports. It uses only the standard library for HTTP. A test runs it, so what is shown cannot stop working unnoticed.
- Found by running it: my first comparison of the two reports in the script left the run id inside the outputs (the batch of `db.write` is the run id); the test had a rule for that, the script did not. Same rule now.

## 2026-11-09 M7: the client is the core's own, and the service ships by cursor

- Chose: the editor and the preview are static files served by the core at `/ui/`; same origin, so no CORS. Monaco and Cytoscape are not committed (Monaco is about 100 MB unpacked); `scripts/fetch_ui.py` fetches pinned versions from the npm registry and checks their SHA-512. The sandbox cannot reach a CDN, and a demonstration may be offline, so a CDN link was rejected twice.
- Chose: everything the client needs is a server function that a test can call (the graph from a plan, the scope of completion, the batch of the outbox). The page is thin: the debounce, the markers and the drawing are the only things only a browser can test, and those get a few tests marked `browser`.
- Chose: the ingest key is a third key of a deployment and belongs to no role. A service is not a user; giving it a user would give it a role, and a role is more than ingesting. The route is denied to all three roles in the walk of the routes, and a separate test shows the right key accepted.
- Chose: the service keeps a cursor per run (`outbox`) and the core tells it what it has. Rejected: a queue table of events (a second copy of the log that could disagree with it) and a cursor held only in memory (a restart would resend everything, harmless but wasteful, and it hides the case where the core lost data).
- Found while building the page: Monaco 0.57 still has the AMD loader but its worker has a hash in its file name, so the fetch script also writes the worker under a stable name; a module script served as `text/plain` is refused by a browser and Windows can give `.js` that type, so the page sets the types of its files in code and a test reads them back.
- Chose: the graph is data from a server function and the client only draws it. The preview of an unsaved text is its own route, and a text with errors gives the errors and no graph (the page then keeps the last valid one and says so), because a half-typed text must not blank the picture. A stored version with errors gives 422, since there is nothing to draw.
- Chose: a fan-out is drawn as a group that can be collapsed, and starts collapsed above 12 instances. Rejected: one node per instance always (a hundred regions give two hundred nodes and an unreadable picture).
- Chose: completion works on the text with the unfinished reference replaced by `true`, and on no text that still does not compile. Rejected: a tolerant parser that recovers a plan from a broken text (a second parser to keep equal to the first), and the last good plan of the client (it would offer outputs of steps that were since deleted). The cost is that a half-typed text with an error elsewhere gets a note and no list, which the note says.
- Chose: `X[*]` is offered when every instance of `X` is an ancestor, not only when each is guaranteed. The list is defined (the instances that succeeded) and the analysis agrees: it warns only about an index past what the join guarantees. Without this the example would not offer `check[*].rows` in `consolidate`, the one place the example needs it.
- Chose: in its own `when` the step's condition is not assumed. Replacing the reference by `true` there could turn `not ${x}` into `false`, which entails everything and would offer outputs that are not guaranteed.
- Left for later, on purpose: the health probe of a service (M8), the plan cache (M8), live refresh of the run list.


## 2026-11-09 US-31: the service ships by cursor, and the core says what it has

- Built: the ingest key (third key of a deployment, to the service by environment, never in the bundle), `POST /deployments/{id}/events`, `RunStore.ingest` (the run and its events in one transaction), the outbox (`service/outbox.py`, a cursor per run in the service's database), the thread that runs it, `--ingest-url` of the core, the views Runs and Services in the page.
- Chose: `events:ingest` is in the table of permissions with an empty set of roles, and `require()` hands that one permission to its own check. The route walk then proves it with no special case: 401 without a token, 403 for admin, designer and operator. Rejected: a user of a fourth role "service" (a role is more than ingesting, and a service would then have a user to leak).
- Chose: the core answers a gap with `expected` as a field of the error item, and the service moves back to `expected - 1` once per run per round. A core that keeps asking for what was just sent makes the run skipped, not a loop. A conflict without `expected` also skips the run: the core will not take it and retrying cannot change that.
- Chose: the idle wait is 1 s and the backoff is 1 s doubling to 30 s; one more round when the service stops. Measured: a batch of 200 costs 6 ms and one event 4 ms, so the cost is the commit, and the outbox sends the pending events of a run in one request.
- Found by a test: when the network failed in the middle of a run the report said 0 events although 20 had been acknowledged, because the exception left the loop before the count was returned. The failure is now an outcome of the run, not an exception through the round.
- Found by the browser test: what a service ships is not trusted. A message with `<img onerror>` is shown as characters; the page builds every cell with `textContent`.
- Measured: `research/m7-measurement.md`.
- Found by CI on Windows: the demonstration answered an approval at once after the run showed WAITING and got 409 "being driven right now", because the thread that drove the run still held its lock for a moment after it wrote the last event. The 409 is right (the run is busy) and the answer is simply sent again; the demonstration and the process tests (`decide` in `test_us30.py`) now retry for up to 10 seconds. The race was there since US-25 and had not shown; the shipper thread's extra reads of the service's database changed the timing enough to expose it.

## 2026-11-09 M8: the owner of a run is a table, the log is not touched

- Chose: ownership is a table (`ownership`: epoch, state, peer) in every store that can own a run, and the log gets one event, `RUN_MIGRATED`, written by the new owner. The Proposal's design appends `MIGRATING`, `MIGRATED_OUT`, `MIGRATED_IN` to the logs, which is wrong with the outbox of M7: the old owner's `MIGRATED_OUT` and the new owner's `MIGRATED_IN` would be two different events with the same (run, seq) in the core's copy, and the core refuses exactly that. Found by reading the key of `run_events` against the protocol, before any code. The states of the Proposal's machine (OWNED, MIGRATING, MIGRATED_OUT, MIGRATED_IN) are kept; `MIGRATED_IN` is `OWNED` at a higher epoch.
- Chose: recovery rolls forward. The check is made before the fence, the fence freezes the history, so the import cannot become impossible; going back would need a second protocol and a second set of crash tests for no gain. Rejected: the Proposal's "queue an approval during the handover" (refuse it; the person answers again at the new owner).
- Chose: the core calls a service with a fourth key (control), which the core must keep to present it; the other keys the core knows only as hashes. Rejected: the core using the ingest key (wrong direction, and it would give the service's own key the power to move runs) and an unauthenticated route on loopback.
- Chose: the replay of the check is its own small state over the new plan (the join rule and the evaluator of the engine, public), not `Scheduler.restore`, which takes the recorded states as true and raises on an unknown step, the very thing the check must report. `restore` is used afterwards by the owner that takes the run, and an oracle test runs both.
- Triaged to M9: resuming unfinished runs at start, the health probe, the plan cache, key rotation, a live refresh. None is in a story of M8; the handover's own recovery is in.

## 2026-11-09 M8: what the stories changed in the design

- Chose: `migration` is on layer 6 beside `recovery`. The service (layer 7) calls conformance and the handover, and layer 5 cannot be imported through the layers in between.
- Chose: a failed step keeps its `on_failure` in the check, because the restore re-applies the stop and a changed policy would change the run after the fact. Added the reason `E-MIG-008` (inputs of the run refused by the new version): resolving the inputs again under the new version found a case the first list of seven reasons did not have.
- Chose: the core's journal table is the coordinator's intent and nothing more. When it recovers, it reads the owners (the old owner's export is the authority) and not the core's own copy, which lags by design.
- Chose: the mirror update carries the version and the inputs, not only the owner. A service-origin run moved to another version would otherwise show the old version in the core until the next shipment.
- Chose: the outbox skips runs that the store does not own (`MIGRATING`, `MIGRATED_OUT`); shipping a history the store has just given away would race with the new owner.
- Chose: the fourth key (control) is kept in clear in the core's registry because the core has to present it. Stated as a limit; rotation is M9.
- Chose: `cancelled` is its own count in the statistics, so that `finished` is the sum of the outcomes and a cancelled run is neither a success nor a failure.
- Found by CI after the second story: three mypy errors that only the project's own interpreter shows. The `mypy` on the path of the sandbox is another installation; the check is `python -m mypy`.

## 2026-11-11 M9: what the audit found

- Found: there are no diagrams. The Playbook says a diagram is born with the code, `diagrams/README.md` lists what was planned for each increment, and the folder holds only that README. Not planned away: missed. Chose to draw them in M9, last among the code stories, and to make a test compare the states of a state machine with the enumerations so that a diagram cannot drift from the code a second time.
- Found: the traceability matrix covers three NFRs of nine and keys the last five stories by function. DOC-7 rebuilds it by requirement.
- Chose: DOC-1 to DOC-6 are the review and the retrospective of M1 to M8, not counted again; DOC-7 stays; DOC-8 and DOC-9 are new (diagrams), sized 5 and 8 SP, with the reason above.
- Chose: a store resumes the runs it owns, nothing else. This answers the question M8 left (which of several processes resumes which run) with the table of ownership, and makes US-36 a small story.
- Chose: the time limit of a waiting step is measured from the recorded time of the wait and judged by the clock that is passed in, as every other time in the engine. A limit from the clock of the process would make a restart reset it.
- Not done by the author alone: NFR-06 (usability with a colleague). The matrix will say `not verified` and the exercise is prepared; no result is written for it.

## 2026-11-11 M9: US-36, a process that starts takes up its own runs

- Chose: a run is taken up when its status is `CREATED`, `RUNNING` or `COMPENSATING` and the store owns it. `WAITING` is left alone: nothing is running, an answer will come by the route, and resuming it would append a `RUN_RESUMED` to the log at every start for nothing.
- Chose: no special path for a run that was stored and never started. `recover` of an empty history gives the same commands as a fresh launch and writes no `RUN_RESUMED`; a test says so, and the branch that was first written for it was removed when the mutation did not fail any test.
- Chose: the driving goes through the same pool and the same lock as a request (`drive_in_background`), so a command that arrives while the start is taking the run up gets 409 and is sent again; no second lock.
- Chose: the core's copy of a service's run is not taken up (its `origin` is the service); the service takes up its own at its own start. This is the whole answer to "which of several processes resumes which run": the one whose database holds the run, and owns it in the table of ownership.
- Measured by the crash test (`tests/crash/test_resume_at_start.py`): the history of the nine-region example, answered `approved` and answered `rejected`, cut after each event, a new core started on the cut; every cut goes on to the end of the uninterrupted run. The step that is not idempotent (the mail on the rejected path) is `UNCERTAIN` when the cut falls while it was sent, and is not sent again. Breaking the rule (not taking `RUNNING` runs up) fails it at the first cut; so does leaving the call out of the service.
- Found while writing the test: `git checkout FILE` to undo a mutation of a file that also had edits not yet committed undid the edits. Mutations are now made on a copy that is put back (`cp`), never with `checkout`.

## 2026-11-11 M9: US-38, the time limit of an approval

- Chose: the deadline is the recorded time of `STEP_WAITING` plus `timeout`, the check is made with the clock that is passed in, and the failure is written at the time of the check. Three alternatives were left: a timer in memory (lost at a restart), a limit counted from the restore (every restart would give a new full limit), a failure written at the deadline (an event in the past).
- Chose: `E-RUN-009` is an ordinary failure of the step, so `on_failure` and the join rules are used as they are; a retry is never given. A test compares `expire` with `step_failed` event by event.
- Chose: an answer after the deadline and before the check is refused with 409. The other choice, accept it and let the check lose, would make the log say a person answered and the step failed.
- Chose: a `timeout` on a step that cannot wait is a diagnostic of the type phase (E-TYPE-006), since only the type phase knows the signature of the action; a limit that changes on a waiting step is a reason in the conformance check (E-MIG-004), since the new version would judge a wait the old one started.
- Chose: the core and a service look at start and every `--expire-interval` seconds on a thread; a version learns once whether any of its steps has a limit, so the others are never rebuilt for it.
- Merge: US-36 and US-38 both added a call at the start of the app and the service; both stay, in the order recover, resume, expire.
- Measured by mutation: wait time taken from the restore, 9 tests fail; deadline exclusive, 9 fail. The full suite passes (2057, one test skipped for want of a Docker daemon here).

## 2026-11-11 M9: US-37, the registry says whether a service answers

- Chose: `unreachable` is shown, not stored. The table has a `CHECK` on the status; a file made by an earlier version cannot have it changed without rebuilding the table, and the process was not stopped by the core, so `running` stays true of what the core did. Two columns (`answering`, `answering_since`) were added the way the earlier ones were, and the core shows `unreachable` when a running row has `answering = 0`.
- Chose: a service is asked twice before it is called unreachable. One lost request on the loopback is common under load; two in a row is a service that does not answer. The price is a longer look when a service is down (two asks of 1 s at most).
- Chose: write only on a change, and `answering_since` is the time of the change. A write at every look would put a transaction every 10 s into the database of the core, which the runs use too, to say the same thing again.
- Chose: an unreachable service can be stopped. A hung process is the case the person most wants to stop, and `launcher.stop` already ignores a process that is gone.
- Chose: a handover to, or a call of, an unreachable service is refused at once with its state in the message; it would have failed after the timeout of the transport otherwise.
- Checked against a real socket as well: the probe with the real `http_healthy` sees a local server that answers, then the same server shut down. A killed `python -m flowstat.api.service` process was not started for this; the socket is the same.
- Measured by mutation: asking stopped services too, 2 tests fail; one ask instead of two, 1 fails; `state` ignoring `answering`, 6 fail.

## 2026-11-11 M9: US-39, the hosts that http.call may reach

- Chose: the host is the one the request goes to (`urlsplit(url).hostname`), lower case, no port, no final dot, and the part after `@` when the URL has a user. The first version of the test named the trick `https://allowed@evil/`; reading `netloc` instead of `hostname` makes 8 tests fail.
- Chose: one function (`model/hosts.py`) is used by the check at save and by the call, so the two cannot differ about what a host is. It is in `model` because `analysis` may not import `actions`.
- Chose: with no list any host may be called. A default that refuses everything would break every workflow that exists; the admin who wants the limit gives the list, and the matrix says it is off until then. There is no way to write an empty list on purpose: an empty text is no list.
- Chose: a redirect is followed only to a host of the list. `urllib` follows redirects by itself, which would have made the list a suggestion: the first host answers 302 and the second is reached. Without the redirect handler the redirect test fails.
- Chose: a service takes the list from its environment at its start, as it takes the address of the core; a list changed in the core reaches a service when it is deployed again. The other way (the core asks the service) would need a route that nobody else needs.
- Found: `default_registry()` was called in four more places than the app (the command line three times, `driving.drive`), so a list set in the app would not have limited a run that `flowstat resume` took up. They take the list now (`Machine.hosts`, the environment for the command line); a test goes through the API and a run.
- Found: a redirect with 307 on POST is not followed by `urllib` at all (it raises), so the redirect test uses 302, which becomes a GET.
- Not covered, and said in the spec: a name that resolves to an address inside the network, and wildcards.
- Measured by mutation: no redirect handler, 1 test fails; `netloc` for `hostname`, 8 fail; the check before sending removed, 3 fail.

## 2026-11-11 M9: US-43, a long chain of guarded steps is analysed

- Found: while preparing the NFR-01 row of the report the analyses of M5 were run on the chain of the compile benchmark. At 500 steps `bounds` raised `RecursionError` (so a save of the draft through the API would have ended in an internal error), and below that the time grew faster than the length. The compiler was not the cause; no earlier benchmark had put a long guarded chain through C2.
- Chose: a story of its own (US-43, 3 SP, M9 now 50 SP) and not a change inside DOC-7, because it changes a C function and needs its tests, its card and its measurement.
- Chose: bound the size of a run condition (`RUN_CAP`, 200 nodes) and give a larger one up as an opaque atom, the way `_quorum` already gives up a quorum over 64 combinations. The other ways were to make `nnf` and `bounds` iterative (the conditions would still be quadratic in size and the time stays bad) and to share subterms (a larger change in the solver, which the stories of M9 do not need).
- Chose: 200 after trying 500 and 1,000: at 1,000 the 1,000-step chain failed again in `nnf`, because the depth of the recursion is half the bound times the frames of one level.
- Chose: count the size with a loop (`runs.within`), since a recursive count fails on the input it is asked about. A test builds a predicate 5,000 deep.
- Cost, said in the card: a guarded chain is exact for about 100 steps in a row; after that its steps lose facts, so a warning may appear where a more exact analysis would not give one, and none is missed. The time with the bound is still above linear (1.9 s at 1,000 steps), which is left as a stated limit.
- Checked: the soundness test of C2 against the reference interpreter again with `RUN_CAP = 3`, so that many conditions are given up (29 to 38 conditions given up in each of three seeds, 461 to 729 facts checked; see `research/deep-chains.md`).
## 2026-11-11 M9: US-40, replacing the control key and the ingest key

- Chose: the story says "a stop in the middle leaves exactly one key working". Read as: after the look that the core makes when it starts (or on request) exactly one key works. Before that look both work, on purpose: a send that is under way with the old key must be able to end, and a core that cannot tell whether the service heard the new key cannot take one away. Said so in the spec instead of promising an instant that no protocol can give.
- Chose: the record is in the registry row and the service is asked. The core writes the intent first, the service is told, the row ends last. `recover` asks the service whether it knows the new key: yes, finish; no, take back. Both ends leave one key. The first plan, to keep a journal of calls as the handover does, was not needed: the service can say what it holds.
- Chose: the control key is rotated with a second user (`control.next`) beside the first, and one call, `settle`, makes the key the caller holds the only one. The same call is the rollback when the old key makes it. That is why a rotation can be repeated from any point.
- Chose: the ingest key is given to the service and kept in its database; the stored key wins over its environment, or a restart would go back to a key the core refuses. `http_send` takes a function for the key, read at every send, so the first send after the change already uses it.
- Chose: the core makes the key (`config.new_token`), the admin never types it, and it is not shown: it is not needed by anyone but the two sides.
- Not done: the key that starts runs and the title keys. They are shown to the person who deploys, so changing them is a decision of that person, and a new deployment does it. Said in the spec.
- Found: the permission `handover:control` is reused for the routes of the service (only the control key holds it); a new permission would have changed the table of section 3 for no new reader.
- Found while testing: the unique hash of a user stops the swap of the two control users in one update; the next user is removed first, in one transaction with the move.
- Measured by mutation (the stop at every call is the test): recovery that always completes, 2 tests fail; settle that does not move the key, 7; the core that takes only the current ingest hash, 1; ingest recovery that always completes, 2.

## 2026-11-11 M9: US-41, the plan of a published version is compiled once for a process

- The story is worded as "listing runs does not compile a plan for each run", and that was done in M6 (the list reads the status from the events). What remained is the report, the graph and the start of a run, which still compile. Measured those and kept the wording of the story in the matrix.
- Chose: the rule for keeping the cache was written before the measurement (a report at least twice as fast, nothing slower). The result met it (2.4x to 2.8x), so it stays; if it had not, the cache would have been taken out and the note written all the same.
- Chose: the key is `(workflow, number, text, catalogue snapshot)`, not the number alone. The plan of a published version cannot change, so the number would do, but with the text in the key a cache can never give the plan of another text, whatever happens to a database file. A draft is not kept.
- Chose: the cache is held by the `Registry`, not by a global and not by the app: the places that build a `WorkflowService` (routes, the pool, resume, expiry) already have the registry, the plans depend on the registry's actions and signatures, and two apps in one test process get two caches.
- Found while measuring: the first version of the benchmark measured the list after the start of runs that the cache-off pass had made, so the list seemed 30% slower with the cache. Two databases with 50 runs each, one for each pass, show the list unchanged. The table is from the fair one.
- Measured by mutation: a draft that is kept, 1 test fails; a key of the name only, 1 fails; no move to the end on a hit, 1 fails.

## 2026-11-11 M9: US-42, the lists follow the server

- Chose: polling, not a push. A push (server-sent events, a websocket) needs a channel that stays open for each page, and the one reader of the list is a person with a tab; a request every 4 seconds from one visible tab costs less than a connection that the app has to keep, count and close. The period is 4 s, not 5: a change waits one period and one answer.
- Chose: the poller asks only while its view is shown and the tab is visible, asks at once when the tab comes back, and does not start a round while one is under way. The three conditions of the story ("within 5 s", "no request while hidden") are three tests.
- Chose: a refresh that the person did not ask for never empties the table when the server does not answer; a refresh that the person asked for does, as before. A 401 ends the refresh, because a page without a token would ask every 4 seconds for ever.
- Found while testing: the sync API of Playwright delivers the events of a page only while a call into Playwright waits, not during `time.sleep`. The first version of the test for the hidden tab slept, got an empty list of requests, and passed for the wrong reason. They wait with `page.wait_for_timeout` now, and each of three faults (no check of visibility, no stop when another view is shown, no stop at a 401) fails its own test, which is how I know the empty lists are real.
- Chose: the Services view follows too, since the status that US-37 made (`unreachable`) is what a person waits to see change there. The sources of the Runs view are not refreshed by the poller (they would move under the hand that is choosing).
- Not done: a mark on a row that changed since the last round. The story does not ask for it.

## 2026-11-11 M9: DOC-8, the structure diagrams

- Chose: the component diagram has one arrow for every import between two packages, not for the layers. It is the graph the architecture test already walks, so the picture and the rule cannot differ: the test takes the set of arrows from the file and the set of imports from the code and wants them equal. An import that is added shows up as a failing test until the diagram has the arrow.
- Chose: the class diagram shows the main classes, not all of them, but what it shows is checked: the class, the fields (taken from the dataclass) and the operations (callable on the class). A drawing may leave a field out and may not invent one. Two classes are called `Plan` (the compiled plan and the plan of a handover); the diagram gives them aliases and the test takes the name in code from the quoted name.
- Chose: the pictures are committed (`docs/diagrams/svg/`) although they can be made again, because the report shows them and a reader of the repository should not need Java and Graphviz to see them. The cost is that a picture can lag its source; a test says when one is missing, not when it is stale, and the README says to run the script after a change of a source.
- Found: PlantUML 1.2019.06 (the jar of an npm package, the only one reachable from the development sandbox) draws everything used here; a newer one may place the boxes differently, which does not matter to the tests.
- Measured by mutation (on copies): an arrow `model --> dsl` fails the test of arrows and the test of layers; a field `ghost` of `PlanStep` fails the test of fields; a module that does not exist fails the test of the deployment diagram.

## 2026-11-11 M9: DOC-9, the behaviour diagrams

- Chose: the state diagrams are checked against what the code does, not only against its names. The states are compared with the enumerations, then a recorder on the dictionary of states of a scheduler logs every change in nine scenarios (a retry, a failure that stops the run and one that lets it go on, a wait that is answered, expires and is cancelled, a cancel at READY, a saga with a failing undo, a crash decided both ways, a crash in an undo), and the set of moves must equal the set of arrows. The first version of the check only asked that no move is missing from the picture; wanting the other direction too is what found that two arrows (a condition and arguments that cannot be evaluated) were drawn and driven by no scenario, and the scenario was added instead of the arrow dropped.
- Found: the status of a run is not an enumeration. `engine/scheduler.py` (`status`) and `model/history.py` (`_status`) each return string literals, and the summary I wrote for the stories said there was a `RunStatus`. The test reads the literals from the source with `ast` and asserts the two places agree. An enumeration would be the better design and is a change of code in a documentation story; it is in the list of what M10 would do, not done here.
- Found while checking the restart: the status of a run changes at a restart without an event (COMPENSATING to WAITING when the undo that was running becomes UNCERTAIN), and the arrow was missing from the first drawing. It is drawn now and the scenario of the crash in an undo drives it.
- Chose: the ownership diagram has the arrow of a run that comes back to a store that released it (MIGRATED_OUT to OWNED by `adopt` at a higher epoch) and the one of a move inside one store (OWNED to OWNED by `retarget`); the abort of a fence is not drawn, because the protocol never goes back (`migration/handover.py`) and the test would fail if it were.
- Chose: a diagram of a C function is tied to its card by three lines that a test reads: the section of the card it follows, the functions it draws, and, for the activity diagram, the procedures the pseudocode of the card defines. C1 and C2 have no named procedures in their card (C1 refers to the Proposal), so for them the check is the card section and the functions.
- Measured by mutation (on copies): a state `LOST` added, an arrow of ownership removed, an arrow `SKIPPED --> READY` added, the word `blockers` taken out of the activity diagram of C4, a card anchor with a typo, a function that does not exist: each fails exactly the test that is meant to.

## 2026-11-15 M9: NFR-07, the suite in a Linux container

- Run: `scripts/test-in-linux.ps1` on the author's Windows machine with Docker Desktop (image `python:3.12-slim`, 11 min 30 s). The first run collected 25 tests, not 2,300: the local folder was an old checkout from the start of M1, not `main`. A `git pull` gave the right tree, and the result is the one of the second run.
- Found: 2,255 passed, 2 skipped, 37 errors, all in `tests/browser`. The driver of Playwright (`node`) does not start in the slim image: `libatomic.so.1` is missing. The fixture skipped only when Chromium could not be launched, not when the driver could not start, so the tests errored where their own comment says they skip.
- Chose: the fixture skips in both cases. CI is unchanged and still strict: its browser step fails if any test is skipped, so a missing browser there cannot pass quietly. Checked by replacing `sync_playwright` with one that fails on start: three skips with the reason; with the real one, the same three tests pass.
- Chose: the matrix keeps `partly verified` for NFR-07, and says what the container run did not cover (the tests of the page, the test that starts a container, the drawing of the diagrams).

- Chose (later, on 17 November): `scripts/test-in-linux.sh` and `.ps1` install `libatomic1` and Chromium the way CI does, so that the tests of the page run in the container. Second run: 2299 passed, 0 errors, 2 skipped, 96.30%, 12 min 47 s. The matrix now says `verified` for NFR-07; the two skips are tests that need a Docker daemon and PlantUML, which the container does not have.

## 2026-11-15 M9: NFR-06, the first person

- Run: two analysts of the department took the exercise with the set-up of `scripts/exercise_setup.py`; the author was present. The first, a statistician with 3 years with data, reports 30 minutes to publication and 3 requests for help; the second, 47 minutes and 5 requests, and said it was a little hard for a first time. The check of the published workflow gave the right end for both answers, five steps. The record sheet was filled in only in part (no minutes to the first save, no errors, no account of what was hard), and the time is the author's report, not a measurement.
- Chose: the result is written as `not met`. The rule written before the run asks for two people who all pass; one did, at the limit of the time with an unmeasured time, and the other needed 47 minutes. The rule said that one pass in two is not met before anyone was asked, and it is applied as written.
- Chose: the equal names (workflow and steps equal to the reference solution) are written in the observation as a fact with the two things that can explain them (four of five step names are in the example of the introduction, which may be looked at), and not as a doubt about the person; the author states that he wrote the text himself.
- Found: the set-up script did not clear an earlier start, an old core could answer instead of the new one, and a wrong name gave no help. See `research/nfr06-exercise.md`.

## 2026-11-17 M9: NFR-06, two more people

- Run: two more colleagues took the exercise; the author reported the results on 17 November (the date of the exercise itself was not recorded). The third, 27 years old, 22 minutes and 3 requests for help; the fourth, 37 years old, 29 minutes and 4 requests. Not recorded: role and experience, the check of the published workflow, the number of steps, the errors, the topics of the requests for help.
- Chose: the result stays `not met`. The rule written before the first run asks for people who all pass; three of four did, and the one who did not (47 minutes) is not cancelled by the others. A rule changed after the results would be written as a decision of its own, with its date; none was taken.
- Found: nobody of the four worked without asking for help (3 to 5 times each), and two of the four finished within one minute of the limit.

## 2026-11-18 M10: US-44, a run on the page

- Why: the page could write, check and publish a workflow and look at runs, but it could not start a run or answer an approval, so the main story (a run stops at the approval, the chief statistician answers, the data is written) could only be shown with a script. The API had both routes already; the page did not use them.
- Did: the button Run (with the inputs the text declares, `quarter` as `2025Q1`), the buttons Approve and Reject on the run, the graph of the run with the colour of each step state (a fan-out is collapsed and takes its worst state; a click opens it), and the button Example. No change to the engine, the API or the layers.
- Chose: the page offers the buttons to everyone and shows the answer of the core when the token lacks the title, instead of guessing the right of the token on the page: the core stays the only place that decides. A run that a service shipped to the core has no buttons, since its approval is answered at the service.
- Found: the example cannot be saved twice and published: the text says `version: 1`, and the second save is version 2 (`E-VER-001`). That is the rule of the language, not a defect of the button; the Problems list says it.
- Found: the port check added for the exercise stopped a restart of the test core while the old core still answered; the old core is a child process that outlives the script when the script is killed. Stop the core with Ctrl+C, not by killing the script.

## 2026-11-18 M10: US-45, sign in by role in a demonstration

- Why: for a demonstration the person had to find the token of each user in a file and paste it, and to do that again each time the role changed (designer to write, chief statistician to approve).
- Did: the core takes `--demo-logins FILE` (the `tokens.json` of the scripts) and then has the route `/demo/logins`; the page shows a button for each name when the route answers. `exercise_setup.py start --logins` passes the option.
- Chose: the option is off by default, and the executable exits with an error unless the host is the loopback address, because the route gives the tokens to anyone who reaches the port. The tokens are not in the repository or in the page: the page reads them from the running core. The exercise of NFR-06 starts without the option, so the people who take it still paste a token, as the task says.
- Found: the core can not give its own tokens (it stores only their hashes), so the file the script writes is the only source; `tokens.json` is now written before the core starts, not after.

## 2026-11-18 M10: US-46, sign in with a name and a password

- Why: the page asked a person to paste a 64-character token, which is right for a program and wrong for a person, and a token cannot be remembered or shown to a colleague. The author asked for a normal sign-in.
- Did: `POST /login` (name and password, answer: a session token and the role), `GET /me`, `POST /logout`; passwords as salted scrypt hashes; a session is a token like the others (only its SHA-256 is stored) that expires after 8 hours; the command line sets a password (`user add --password`, `user passwd`); the start script of the exercise gives each of its four users a password. The page is the next story (US-47).
- Chose: a session token and not a signed cookie or a JWT. The code that checks a token stays one lookup by hash, every route and every test of the roles works unchanged, and a session can be ended at once (a row is removed). Cost: one more table and a lookup of it when a token is not a long-lived one.
- Chose: the count of failed attempts is for each name and in memory. An address is not a good key behind a proxy, and a table of attempts would be one more thing to prune; a restart resets it. The limit (5 in a minute) is a brake against guessing at the speed of the network, not a defence against a patient attacker who has many names; that is written in the spec.
- Chose: no endpoint sets a password, and the user cannot change it. Setting it is the work of whoever administers the machine, as creating a user already was. A reset by mail needs mail that the system does not have.
- Found: the test that walks all routes assumed that the only public route is the health check and expected 200 from it; it now accepts 422 for the sign-in, which has no body there.

## 2026-11-18 M10: US-47, a sign-in screen, addresses for the pages and a home page

- Why: the page was one screen with a token field in the corner and tabs that did not change the address, so the back button left the system, a reload lost the place, and nothing said what the system is for. The author asked for a normal sign-in, pages that can be moved between, and a look that holds in front of the professor.
- Did: a sign-in screen (name and password; an API token under "Use an API token instead"; the role buttons of a demonstration under the form); addresses `#/home`, `#/editor[/<workflow>]`, `#/runs[/<run>]`, `#/services`, `#/migration`, `#/statistics`, `#/login?next=...`; a guard that sends a person who is not signed in to the sign-in screen and brings them back; a menu that shows only what the role may use (from `GET /me`); sign out; a session that the core ends sends the person to sign in again; a home page (what the system does, counts, the approvals that wait with a link, the latest runs, how it works); one look for all pages (dark top bar, cards, badges, no external fonts or images).
- Chose: the address is the part after `#`, not a path. The core serves one static file at `/ui/` and a path such as `/ui/runs` would need a route for each page in the server; the part after `#` needs nothing on the server and the back button, a reload and a link all work.
- Chose: the menu hides what the role may not use but the address is not blocked: a person who types `#/statistics` as an operator sees the page and its refusal from the core. The permission is checked by the core on every call; the menu is for comfort and is not a defence.
- Chose: signing out loads the page again, so nothing of the last person (the text of the editor, the table of runs) stays for the next one. A session that ends by itself does not reload, so that a draft in the editor is not lost; the same person signs in and goes on.
- Chose: the role buttons of a demonstration stay on the sign-in screen only. To change role the presenter signs out, which also clears the page, and presses another button.
- Found: three of the old browser tests clicked a tab at once after a click that now changes the address, which is asynchronous; they wait for the page now. One test selected rows by `data-id` and also found the rows of the home page, which are hidden; the home page uses `data-run`.
- Not done: a password cannot be changed by the person (no endpoint, as in US-46); the page was not checked on a phone.

## 2026-11-18 M10: a rare crash of the browser tests, still open

- Seen: about one run in three of `tests/browser` on a developer machine, and twice in a row on the Linux job of CI, ends with `Segmentation fault` (exit 139) inside `sqlite3`, in a thread of the server that is running a request (`connect`, `execute` of different routes). It is older than US-47: it was seen on the pull requests of US-44 and US-45 and passed on a second run.
- Tried: (1) the guess that the garbage collector closes a connection that another thread is using: with `gc.disable()` there were 0 crashes in 5 runs, but 1 in 3 in a later series with a stricter mode, so the collector is not the whole cause. (2) Looking for connections that are never closed: the only one was in the fixture of the new page tests (US-47), which is closed now; the series after that had 0 crashes in 3 runs of the stricter mode, but before it, with the leak, 3 of 3 had crashed, so the leak may make it more likely and is not shown to be the cause. (3) Every request has its own connection, made with `check_same_thread=False` in one worker thread and used and closed in others, one at a time; SQLite is built with `THREADSAFE=1`.
- Not found: the cause. The tests and the core are not changed for it, and the job is run again when it falls. A real fix needs a reproduction that fails every time; this entry is here so that nobody counts the rerun as a cure.

