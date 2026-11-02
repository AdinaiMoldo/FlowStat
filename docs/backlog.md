# Backlog

The full backlog (34 user stories, 203 SP baseline) is in the Project Proposal. This file is the live copy with statuses; move the stories here at the start of I1.

| Milestone | SP | Stories | Goal | Status |
| --- | --- | --- | --- | --- |
| M0. Preparation | - | - | Project Proposal, Engineering Playbook, repository skeleton, architecture tests, CI | done |
| M1 (I1). Language core | 26 | US-01 to US-05 | Version store, catalogue, YAML reading, expression parser and shape validator, C1 phases 0-3 | next |
| M2 (I2). Engine | 38 | US-06 to US-12 | C3 with plain longest path, history, step actions, CLI | not started |
| M3 (I3). Fault tolerance | 28 | US-13 to US-17 | C4: write-ahead log, replay, saga | not started |
| M4 (I4a). Solver | 19 | US-20, US-23, US-24 | C6 solver and test generator, reference interpreter | not started |
| M5 (I4b). Analysis | 26 | US-18, US-19, US-21, US-22 | C1 phase 4 (types), C2, feasible critical path | not started |
| M6 (I5a). API, roles and deployment | 21 | US-25, US-29, US-30 | REST API, roles, packaging a workflow as a service | not started |
| M7 (I5b). IDE and service view | 21 | US-26, US-27, US-28, US-31 | Monaco editor, graph preview, completion, runs of deployed services in the core | not started |
| M8 (I6). Evolution | 24 | US-32 to US-34 | C5 (conformance first, handover second) and statistics | not started |
| M9. Hardening | not estimated | - | Coverage gate, final diagrams, traceability matrix, report assembly | not started |

The split of I4 and I5 into two milestones each follows the size of the FleetSync sprints (8 to 29 SP). The SP of M4 and M5 exclude the reference interpreter and the feasible critical path, which are counted when M4 and M5 start. M9 is not in the 203 SP baseline.

Not yet estimated: parser and shape validator (about +6 SP net, counted at the start of I1), feasible critical path (about 5 SP, counted at the start of I4), optional additions in the Playbook (section 3).

| Story | Title | Function | SP | Priority | Milestone | Status |
| --- | --- | --- | --- | --- | --- | --- |
| US-01 | As a designer, I save a workflow as a new version so that published versions never change | A1 | 5 | Must | M1 | not started |
| US-02 | As a designer, I pick regions and forms from a catalogue | A4 | 3 | Must | M1 | not started |
| US-03 | As a designer, I get a precise error when my text is not a valid workflow document | B1 | 5 | Must | M1 | not started |
| US-04 | As a designer, I see unknown references and cycles with line numbers | C1 | 5 | Must | M1 | not started |
| US-05 | As a designer, I fan a step out over all regions | C1 | 8 | Must | M1 | not started |
| US-06 | As an operator, I run a workflow and independent steps run in parallel | C3 | 8 | Must | M2 | not started |
| US-07 | As an operator, I get correct skips, failures and quorum behaviour | C3 | 8 | Must | M2 | not started |
| US-08 | As an operator, I approve or reject a run that waits for me | C3 | 5 | Must | M2 | not started |
| US-09 | As an operator, I see an explicit outcome for every run | C3 | 3 | Must | M2 | not started |
| US-10 | As a designer, I declare a contract on a step's output | C3, C1 | 3 | Must | M2 | not started |
| US-11 | As an operator, I see the history of a run | A2 | 3 | Must | M2 | not started |
| US-12 | As a designer, I read a regional xlsx form and write results to the database in steps | B6 | 8 | Must | M2 | not started |
| US-13 | As an operator, transient errors are retried | C4 | 2 | Must | M3 | not started |
| US-14 | As an operator, a failed run is compensated in the right order | C4 | 8 | Must | M3 | not started |
| US-15 | As an operator, a run survives an engine crash | C4 | 8 | Must | M3 | not started |
| US-16 | As an operator, I decide about steps whose outcome is unknown after a crash | C4 | 5 | Must | M3 | not started |
| US-17 | As a developer, I test recovery against an action server that misbehaves | C4 | 5 | Must | M3 | not started |
| US-18 | As a designer, I see type errors in parameters and conditions | C1 | 8 | Must | M5 | not started |
| US-19 | As a designer, I get correct types for fan-in references | C1 | 5 | Should | M5 | not started |
| US-20 | As a designer, I am told when branches overlap or do not cover every case | C6 | 8 | Must | M4 | not started |
| US-21 | As a designer, I am warned about outputs that may not exist | C2 | 8 | Must | M5 | not started |
| US-22 | As a designer, I am warned when a goal or a quorum is not guaranteed | C2 | 5 | Should | M5 | not started |
| US-23 | As a designer, I learn about steps that can never run | C6 | 3 | Should | M4 | not started |
| US-24 | As a designer, I get a generated minimal test set | C6 | 8 | Should | M4 | not started |
| US-25 | As an external system, I start a run over REST | B4 | 5 | Must | M6 | not started |
| US-26 | As a designer, I edit in an IDE with live diagnostics | B2 | 8 | Should | M7 | not started |
| US-27 | As a designer, completion shows only data available at the cursor | B2, C2 | 3 | Should | M7 | not started |
| US-28 | As a designer, I see the workflow as a graph | B3 | 5 | Should | M7 | not started |
| US-29 | As an admin, I restrict publishing to designers | A3 | 3 | Should | M6 | not started |
| US-30 | As an admin, I deploy a workflow as a separate service | B5 | 13 | Must | M6 | not started |
| US-31 | As an admin, I see the runs of deployed services in the core | A6, A2 | 5 | Must | M7 | not started |
| US-32 | As a designer, I learn which running instances can move to a new version | C5 | 13 | Should | M8 | not started |
| US-33 | As an operator, I move a migratable run to the new version | C5 | 8 | Should | M8 | not started |
| US-34 | As an admin, I see failure rate and average duration per workflow | A5 | 3 | Could | M8 | not started |

Acceptance criteria: `stories.md`. The same stories are GitHub issues, grouped by milestone.
