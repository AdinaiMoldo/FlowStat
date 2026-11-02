# User stories

Generated from the Project Proposal (backlog table). Acceptance criteria in Given/When/Then form. Status lives in `backlog.md` and in the GitHub issues.


## M1

### US-01: As a designer, I save a workflow as a new version so that published versions never change

Given v2 is published, when I save edits, then v3 is created as a draft and v2 is unchanged

Function A1 · 5 SP · Must

### US-02: As a designer, I pick regions and forms from a catalogue

Given the catalogue has 9 regions, when I use ref: regions, then the input accepts only those codes and the version stores a snapshot of the catalogue

Function A4 · 3 SP · Must

### US-03: As a designer, I get a precise error when my text is not a valid workflow document

Given a missing colon on line 14, when I validate, then one error points to line 14 and its column. Given two steps with the same id, then the error points to the second

Function B1 · 5 SP · Must

### US-04: As a designer, I see unknown references and cycles with line numbers

Given b needs c, which does not exist, when I validate, then an error points to the line of c. Given a to b to a, then exactly one error lists the path a, b, a

Function C1 · 5 SP · Must

### US-05: As a designer, I fan a step out over all regions

Given foreach region in catalogue.regions and 9 regions, when I publish, then the plan has 9 instances load[key]. Given an error in the template, then it is reported once, at the template line

Function C1 · 8 SP · Must


## M2

### US-06: As an operator, I run a workflow and independent steps run in parallel

Given a and b are independent, c needs both and m = 2, when I run, then a and b overlap in time and c starts after both succeed

Function C3 · 8 SP · Must

### US-07: As an operator, I get correct skips, failures and quorum behaviour

Given b has a false when, c needs b (all_success) and d needs b (all_done), when I run, then b and c are SKIPPED and d runs. Given 9 loads, 2 failing and at_least: 7, then consolidate runs; with 3 failing it is UPSTREAM_FAILED

Function C3 · 8 SP · Must

### US-08: As an operator, I approve or reject a run that waits for me

Given a run reaches approval.wait, then it is WAITING and releases its slot. When I answer rejected, then the step is SUCCEEDED with decision rejected and the branch guarded by that decision runs

Function C3 · 5 SP · Must

### US-09: As an operator, I see an explicit outcome for every run

Given goals: {any_of: [publish, return_to_regions]} and a rejection, then return_to_regions runs and the run is SUCCEEDED. Given a run in which no goal succeeded, then it ends as GOAL_NOT_REACHED

Function C3 · 3 SP · Must

### US-10: As a designer, I declare a contract on a step's output

Given ensures: publish.written > 0 and an action that writes 0 rows, when it runs, then the step fails with a contract violation

Function C3, C1 · 3 SP · Must

### US-11: As an operator, I see the history of a run

Given a finished run, when I open it, then each step shows status, start, end and error message

Function A2 · 3 SP · Must

### US-12: As a designer, I read a regional xlsx form and write results to the database in steps

Given a valid form file, when xlsx.read runs, then its output has the declared schema. Given a missing file, then the step fails with the path in the message

Function B6 · 8 SP · Must


## M3

### US-13: As an operator, transient errors are retried

Given retry: {max: 3} and an action failing twice, when I run, then the step succeeds on the third attempt with growing delays

Function C4 · 2 SP · Must

### US-14: As an operator, a failed run is compensated in the right order

Given a, then b succeeded, both with compensate, and c failed under the compensate policy, then compensation of b finishes before compensation of a starts and the run is COMPENSATED

Function C4 · 8 SP · Must

### US-15: As an operator, a run survives an engine crash

Given the engine is killed while idempotent b is RUNNING, when it restarts, then a is not re-run, b runs once more with the same idempotency key, and the run completes

Function C4 · 8 SP · Must

### US-16: As an operator, I decide about steps whose outcome is unknown after a crash

Given the engine is killed while a non-idempotent step is RUNNING, when it restarts, then the step is UNCERTAIN and nothing is re-run automatically; I can mark it done or retry it

Function C4 · 5 SP · Must

### US-17: As a developer, I test recovery against an action server that misbehaves

Given the server executes a request and drops the response, when the engine retries with the same key, then the effect exists exactly once

Function C4 · 5 SP · Must


## M5

### US-18: As a designer, I see type errors in parameters and conditions

Given when: load.rows > 5 where rows is a list, when I validate, then an error says that a list cannot be compared with a number, at that column, and no follow-up errors appear

Function C1 · 8 SP · Must

### US-19: As a designer, I get correct types for fan-in references

Given consolidate takes check[*].rows, when I validate, then the parameter has the type list of rows. Given an action that expects a single rows value, then an error shows the mismatch

Function C1 · 5 SP · Should


## M4

### US-20: As a designer, I am told when branches overlap or do not cover every case

Given publish guarded by decision approved and return_to_regions by rejected, then there is no warning. Given the decision enum also contains escalated, then a warning names escalated as uncovered

Function C6 · 8 SP · Must


## M5

### US-21: As a designer, I am warned about outputs that may not exist

Given c references approve, which has a when, when I validate, then a warning names the condition that can skip approve. Given the guard of c implies the guard of approve, then there is no warning

Function C2 · 8 SP · Must

### US-22: As a designer, I am warned when a goal or a quorum is not guaranteed

Given any_of goals with guards that do not cover every case, then GOAL_MAY_BE_SKIPPED appears with a witness value. Given a reference that needs 8 elements of a group with at_least: 7, then a warning appears

Function C2 · 5 SP · Should


## M4

### US-23: As a designer, I learn about steps that can never run

Given a step whose guard contradicts the constraints on the inputs, when I validate, then DEAD_STEP names it

Function C6 · 3 SP · Should

### US-24: As a designer, I get a generated minimal test set

Given the example, when I request tests, then the inputs produced execute and skip each guarded step at least once where feasible, run with mocked actions, and any difference between predicted and observed skips is reported

Function C6 · 8 SP · Should


## M6

### US-25: As an external system, I start a run over REST

Given a published workflow, when I POST valid inputs, then I receive a run id. Invalid inputs return 422 with field errors

Function B4 · 5 SP · Must


## M7

### US-26: As a designer, I edit in an IDE with live diagnostics

Given an error is typed, when I pause for 500 ms, then a marker appears at its position

Function B2 · 8 SP · Should

### US-27: As a designer, completion shows only data available at the cursor

Given the cursor is inside ${ in step c, when I request completion, then only inputs and outputs of steps guaranteed at c are suggested

Function B2, C2 · 3 SP · Should

### US-28: As a designer, I see the workflow as a graph

Given a valid workflow, when I open the preview, then every step instance is a node and every dependency an arrow; conditional steps are marked and fan-out instances are grouped

Function B3 · 5 SP · Should


## M6

### US-29: As an admin, I restrict publishing to designers

Given a user with the operator role, when they publish, then the API returns 403

Function A3 · 3 SP · Should

### US-30: As an admin, I deploy a workflow as a separate service

Given v4 is published, when I deploy it, then the service answers on its endpoint and returns the same results as the core's executor for the same inputs

Function B5 · 13 SP · Must


## M7

### US-31: As an admin, I see the runs of deployed services in the core

Given a deployed service completes a run, when its outbox ships the events, then the core shows the history. Given the same batch is delivered twice, then no duplicate events appear. The registry shows version, endpoint and status

Function A6, A2 · 5 SP · Must


## M8

### US-32: As a designer, I learn which running instances can move to a new version

Given a run of v3 waiting at approval and v4 adds a step after approval, then the run is migratable. Given v4 changes a completed step, then it is not, with the reason. Given v4 adds a region, then its instances are shown as pending

Function C5 · 13 SP · Should

### US-33: As an operator, I move a migratable run to the new version

Given a migratable run, when I confirm, then the service of v4 owns it and every command to the old owner is rejected. Given a crash during the handover, then after restart exactly one owner remains

Function C5 · 8 SP · Should

### US-34: As an admin, I see failure rate and average duration per workflow

Given 10 runs with 2 failures, when I open statistics, then the failure rate shows 20%

Function A5 · 3 SP · Could
