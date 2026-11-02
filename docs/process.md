# Process

Plan: incremental, nine milestones (`backlog.md`). Working practice: the way FleetSync was run, adapted to one person and to our functions.

## The cycle of a milestone

1. **Goal.** One sentence and the list of stories, written in `docs/milestones/Mx.md` before any code. Every story has acceptance criteria in Given/When/Then form and a size in story points.
2. **Readiness audit.** Before the first line of code, check the milestone against the Proposal: every requirement it touches has a story, every story has a criterion. Gaps found are fixed and written in the journal.
3. **Work in small steps.** One branch per story, the story ID in the commit message, tests before code for C1, C3, C4 and C6. `main` stays green.
4. **Decisions in the journal.** Every significant choice goes to `journal.md` the same day: what we chose, what we rejected, what we measured.
5. **Measurements in research.** Every benchmark and its conclusion go to `docs/research/`, even when the conclusion is "no gain".
6. **Review and retrospective.** At the end of the milestone, in `docs/milestones/Mx.md`: what was delivered against the goal, velocity (SP done), what went wrong, what changes next.
7. **Verification pass.** The report section is checked against the code, not written from memory: every number in it is reproduced by a command.
8. **Tag.** `m1` ... `m9` on the commit that closes the milestone.

## Scope changes

New stories discovered during the work go to the backlog with a size and a reason (and the milestone they land in), never silently into the current milestone. If a story is reopened after review, its entry says why.

## Documents kept alive

| Document | Updated when |
| --- | --- |
| `backlog.md` | every story changes status |
| `journal.md` | every significant decision |
| `milestones/Mx.md` | start and end of the milestone |
| `traceability.md` | a story is closed |
| `algorithms/Cx.md` | a level of a C function is finished |
| `research/` | a benchmark is run |
| `diagrams/` | with the code that the diagram describes, not after |
