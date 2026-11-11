# Backlog

The full backlog (34 user stories, 203 SP baseline, 214 SP planned after the recount of 5 November) is in the Project Proposal. This file is the live copy with statuses; move the stories here at the start of I1.

| Milestone | SP | Stories | Goal | Status |
| --- | --- | --- | --- | --- |
| M0. Preparation | - | - | Project Proposal, Engineering Playbook, repository skeleton, architecture tests, CI | done |
| M1 (I1). Language core | 26 + 6 | US-01 to US-05 | Version store, catalogue, YAML reading, expression parser and shape validator, C1 phases 0-3 | done (5 Nov) |
| M2 (I2). Engine | 38 | US-06 to US-12 | C3 with plain longest path, history, step actions, CLI | done (5 Nov) |
| M3 (I3). Fault tolerance | 28 | US-13 to US-17 | C4: write-ahead log, replay, saga | done (5 Nov) |
| M4 (I4a). Solver | 19 | US-20, US-23, US-24 | C6 solver and test generator | done (5 Nov) |
| M5 (I4b). Analysis | 26 + 5 | US-18, US-19, US-21, US-22, US-35 | C1 phase 4 (types), C2, feasible critical path | done (5 Nov) |
| M6 (I5a). API, roles and deployment | 21 | US-25, US-29, US-30 | REST API, roles, packaging a workflow as a service | done (8 Nov) |
| M7 (I5b). IDE and service view | 21 | US-26, US-27, US-28, US-31 | Monaco editor, graph preview, completion, runs of deployed services in the core | done (9 Nov) |
| M8 (I6). Evolution | 24 | US-32 to US-34 | C5 (conformance first, handover second) and statistics | done (9 Nov) |
| M9. Hardening | 47 | US-36 to US-42, DOC-7 to DOC-9 | What M3 to M8 carried over (resume at start, health of services, time limit of an approval, hosts of `http.call`, key rotation, plan cache, live refresh); coverage gate per package, diagrams, traceability matrix, charts | in progress |

The split of I4 and I5 into two milestones each follows the size of the FleetSync sprints (8 to 29 SP). The SP of M4 and M5 exclude the feasible critical path, which became the story US-35 (5 SP) when M5 started. The reference interpreter (the oracle of C2) moved from M4 to M5 in the readiness audit of M4 and is counted with it. M9 is not in the 203 SP baseline.

M9 (9 November): 47 SP, of which 29 SP are work carried over from M3 to M8 (US-36 to US-42, new stories, each with the reason in `milestones/M9.md`) and 18 SP are the report (DOC-7 of the Proposal, 5 SP, and two stories, DOC-8 and DOC-9, for the diagrams that were planned for the increments and were not made). DOC-1 to DOC-6 of the Proposal (the report section of each increment) are not tracked as stories: the review and the retrospective in `milestones/M1.md` to `M8.md` are those sections.

Recount of 5 November: the baseline of 203 SP stays the zero point of the burndown. Added scope: parser and shape validator +6 SP (M1, done), feasible critical path +5 SP (M5, estimate). Planned total 214 SP. Not yet estimated: the optional additions in the Playbook (section 3).

| Story | Title | Function | SP | Priority | Milestone | Status |
| --- | --- | --- | --- | --- | --- | --- |
| US-01 | As a designer, I save a workflow as a new version so that published versions never change | A1 | 5 | Must | M1 | done |
| US-02 | As a designer, I pick regions and forms from a catalogue | A4 | 3 | Must | M1 | done |
| US-03 | As a designer, I get a precise error when my text is not a valid workflow document | B1 | 5 | Must | M1 | done |
| US-04 | As a designer, I see unknown references and cycles with line numbers | C1 | 5 | Must | M1 | done |
| US-05 | As a designer, I fan a step out over all regions | C1 | 8 | Must | M1 | done |
| US-06 | As an operator, I run a workflow and independent steps run in parallel | C3 | 8 | Must | M2 | done |
| US-07 | As an operator, I get correct skips, failures and quorum behaviour | C3 | 8 | Must | M2 | done |
| US-08 | As an operator, I approve or reject a run that waits for me | C3 | 5 | Must | M2 | done |
| US-09 | As an operator, I see an explicit outcome for every run | C3 | 3 | Must | M2 | done |
| US-10 | As a designer, I declare a contract on a step's output | C3, C1 | 3 | Must | M2 | done |
| US-11 | As an operator, I see the history of a run | A2 | 3 | Must | M2 | done |
| US-12 | As a designer, I read a regional xlsx form and write results to the database in steps | B6 | 8 | Must | M2 | done |
| US-13 | As an operator, transient errors are retried | C4 | 2 | Must | M3 | done |
| US-14 | As an operator, a failed run is compensated in the right order | C4 | 8 | Must | M3 | done |
| US-15 | As an operator, a run survives an engine crash | C4 | 8 | Must | M3 | done |
| US-16 | As an operator, I decide about steps whose outcome is unknown after a crash | C4 | 5 | Must | M3 | done |
| US-17 | As a developer, I test recovery against an action server that misbehaves | C4 | 5 | Must | M3 | done |
| US-18 | As a designer, I see type errors in parameters and conditions | C1 | 8 | Must | M5 | done |
| US-19 | As a designer, I get correct types for fan-in references | C1 | 5 | Should | M5 | done |
| US-20 | As a designer, I am told when branches overlap or do not cover every case | C6 | 8 | Must | M4 | done |
| US-21 | As a designer, I am warned about outputs that may not exist | C2 | 8 | Must | M5 | done |
| US-22 | As a designer, I am warned when a goal or a quorum is not guaranteed | C2 | 5 | Should | M5 | done |
| US-23 | As a designer, I learn about steps that can never run | C6 | 3 | Should | M4 | done |
| US-24 | As a designer, I get a generated minimal test set | C6 | 8 | Should | M4 | done |
| US-25 | As an external system, I start a run over REST | B4 | 5 | Must | M6 | done (8 Nov) |
| US-26 | As a designer, I edit in an IDE with live diagnostics | B2 | 8 | Should | M7 | done (9 Nov) |
| US-27 | As a designer, completion shows only data available at the cursor | B2, C2 | 3 | Should | M7 | done (9 Nov) |
| US-28 | As a designer, I see the workflow as a graph | B3 | 5 | Should | M7 | done (9 Nov) |
| US-29 | As an admin, I restrict publishing to designers | A3 | 3 | Should | M6 | done (8 Nov) |
| US-30 | As an admin, I deploy a workflow as a separate service | B5 | 13 | Must | M6 | done (8 Nov); container test runs on the Linux runner of CI |
| US-31 | As an admin, I see the runs of deployed services in the core | A6, A2 | 5 | Must | M7 | done (9 Nov) |
| US-32 | As a designer, I learn which running instances can move to a new version | C5 | 13 | Should | M8 | done |
| US-33 | As an operator, I move a migratable run to the new version | C5 | 8 | Should | M8 | done |
| US-34 | As an admin, I see failure rate and average duration per workflow | A5 | 3 | Could | M8 | done |
| US-35 | As an operator, steps on the longest chain that can really run start first | C3 | 5 | Should | M5 | done |
| US-36 | As an operator, a run that was unfinished when a process stopped goes on when the process starts | C4 | 5 | Should | M9 | done |
| US-37 | As an admin, the registry shows whether a deployed service answers | A6 | 3 | Should | M9 | not started |
| US-38 | As a designer, I give an approval a time limit | C3 | 8 | Should | M9 | done |
| US-39 | As an admin, I limit the hosts that `http.call` may reach | B6 | 3 | Should | M9 | not started |
| US-40 | As an admin, I replace the key of a deployment without stopping it | A3 | 5 | Should | M9 | not started |
| US-41 | As a user of the API, listing runs does not compile a plan for each run | B4 | 3 | Could | M9 | not started |
| US-42 | As an operator, the lists of runs and services update by themselves | B2 | 2 | Could | M9 | not started |
| DOC-7 | As the author, I assemble the final report from the repository | DOC | 5 | Must | M9 | not started |
| DOC-8 | As a reader, I see the structure of the system in diagrams | DOC | 5 | Must | M9 | not started |
| DOC-9 | As a reader, I see the behaviour of the system in diagrams | DOC | 8 | Must | M9 | not started |

Acceptance criteria: `stories.md`. The same stories are GitHub issues, grouped by milestone.
