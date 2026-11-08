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
