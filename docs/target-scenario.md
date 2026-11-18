# Target scenario: from Tunduk to the website

Status: a description of where the system is meant to go. Only the parts marked **done** exist in
the repository; the others are not built and have not been tried against a real service.

## The scenario

Every quarter the regional offices report to the Committee. In the target scenario nobody sends an
Excel file by e-mail:

1. The data of the nine regions reach the platform from Tunduk, the state platform for exchange
   of data between agencies.
2. The platform checks every incoming report against the statistical rules. A report with a
   mistake stops the run at its check and the platform names the region and the rule.
3. The chief statistician approves the consolidated result, or rejects it.
4. Only an approved result is published on the public website of the Committee. A run that
   failed, did not reach its goal or was rejected publishes nothing, and what was already written
   is taken back.

## What each step is in the system

| Step | Where it is | State |
| --- | --- | --- |
| Receive the data of a region | `http.call` (a request with an idempotency key, retry of transient errors, a list of allowed hosts); today the data come from Excel files that an operator sends on the page Forms (`xlsx.read`) | Excel: **done**. Tunduk: **not built** |
| Check the rules | `statguard.validate` in the step `check[region]`; the first form with a mistake fails the run and names the step, the code and the message | **done** |
| Consolidate with a quorum | `stats.aggregate` with `join: {at_least: 7}` | **done** |
| Approve | `approval.wait` for the chief statistician, answered on the page or through the API | **done** |
| Publish | `db.write` into the results database, with `db.delete_batch` to take it back (saga) | Database: **done**. Website: **not built** |
| Survive a crash, move to a new version | write-ahead log and replay (C4), conformance and handover (C5) | **done** |

## What is missing, and what it needs

- **Receiving from Tunduk.** `http.call` has no client certificate and sends only fixed headers
  (`Content-Type` and `Idempotency-Key`). A state exchange platform is likely to ask for both, but
  the protocol of Tunduk is not known to this project, and finding it out from its documentation
  is the first task. The change itself is small: arguments of the action for headers and a
  certificate, and a step that turns the answer of the service into the rows that
  `statguard.validate` takes.
- **Publishing to the website.** A step that sends the approved result to the website, as a
  second `http.call` (or a database that the website reads). It must keep the property of the
  present publication: it comes after the approval, it has a compensation, and a retry does not
  publish twice (the idempotency key).
- **Real data.** The repository holds only synthetic data. A test against Tunduk needs access that
  the project does not have, so any such step can be checked here only against a stand-in that
  answers like the service is documented to.

## What does not change

The workflow stays the same text: the step `load` reads from another source, the steps `check`,
`consolidate` and `approve` stay as they are, and `publish` has a new target. That is why the
analysis, the engine and the recovery of C1 to C6 need no change for this scenario, and why the
analysis of availability and the tests of the guards go on working on the new workflow.

## What to say about it

Checking, approval, taking back and the reliability of a run are built and shown. Receiving from
Tunduk and publishing to a website are prepared by the architecture (a step that calls another
system with retries and protection against duplicates) and are the next stories, not part of this
delivery.
