# NFR-06: the usability exercise

**Status: run with four people (the first two on 15 November 2026, the other two reported on 17 November); three passed and one did not, so NFR-06 is not met by the rule below.** The requirement
(Project Proposal, NFR-06) is that an analyst of the department writes a valid five-step workflow in
at most 30 minutes after a 15-minute introduction. That is a test with a person, and the author
cannot be that person: she knows the language. This file is everything needed to run it. Four
people have taken it and the results are in the table at the end; the rule below asks for at least
two people, all of whom pass, and one of the four did not, so `traceability.md` says that NFR-06 is
not met.

## Who, where, what is needed

- One analyst of the department who has never written a FlowStat workflow. Not a developer. One
  person at a time; the exercise is run again for each person, and two or three people give a more
  honest answer than one.
- One computer with the editor fetched once (`python scripts/fetch_ui.py`) and the project
  installed (`pip install -e ".[dev,api,deploy]"`). Then one command makes a clean core with the
  synthetic catalogue, the synthetic forms of the quarter `2025Q1`, five users (Adinai, the admin; Azamat, the designer; Alua, the chief statistician; Atai and
  Marina, operators), starts it, and prints the address of the page and the name and password of each user (the person signs in as the designer):
  `python scripts/exercise_setup.py start` (it runs until Ctrl+C, and each start clears
  `exercise-data/` and makes a new database, so each person gets the same one; it refuses to clear a
  folder that an earlier start did not make).
- A second person to keep time and to write on the record sheet: the observer. The observer does not
  help, and the author is not the observer if she can avoid it.
- A timer. The exercise is on the clock only while the analyst writes.

## The introduction (15 minutes, read out, not improvised)

The same text for everyone, so that the result does not depend on the day.

1. *What a workflow is* (2 min). A list of steps. Each step does one thing, by an action, for
   example read a file or ask a person. A step may wait for other steps. The engine decides the
   order; the analyst only says what depends on what.
2. *The page* (3 min). The editor, with the list of problems under it; a problem has a line number
   and a message, and the text is checked when the typing stops for half a second. *Save draft*
   stores the text, *Publish* freezes a saved version, *Show graph* draws the steps and their
   dependencies.
3. *The five words* (6 min), with the finished example of `examples/quarterly_consolidation.yaml`
   open and read line by line, without `foreach` and without `join`:
   - `action:` what the step does; `with:` what it is given; `${load.rows}` is the output `rows` of
     the step `load`;
   - `needs: [load]` the step starts after `load` has succeeded;
   - `when:` the step runs only if the condition is true, otherwise it is skipped;
   - `goals:` the run has succeeded if one of these steps has.
4. *The actions* (2 min). The list the page shows: `xlsx.read` (reads a form, gives `rows`),
   `statguard.validate` (checks the rows, gives `rows`), `approval.wait` (waits for a person with a
   role, gives `decision`), `db.write` (writes rows), `email.send` (sends a message).
5. *Rules of the exercise* (2 min). Thirty minutes. Ask the observer nothing about how to write it;
   the observer answers only what the page does not show (for example, where the Save button is).
   Looking at the example while writing is allowed.

## The task (handed on paper)

> The office of Bishkek (`BIS`) sends its form of the quarter every three months as the file
> `BIS/<quarter>.xlsx`. Write a workflow with **five steps** that does the following.
>
> 1. Read the form.
> 2. Check it.
> 3. Ask the chief statistician (the role `chief_statistician`) to approve it.
> 4. If the answer is *approved*, write the checked rows to the database.
> 5. If the answer is *rejected*, send a message to the office.
>
> The quarter is an input of the workflow, written like `2025Q1`. The workflow is finished when the
> rows are written or the message is sent.
>
> Save it, and when the page shows no error, publish it.

The reference solution is `examples/exercise_one_region.yaml`. It is not shown to the analyst. A test
(`tests/acceptance/test_nfr06_exercise.py`) checks that the task has this solution: five steps, no
diagnostic, and both answers end with the goal reached.

## What counts

The exercise passes for a person if, within 30 minutes of the start of writing:

- the workflow is saved with **no error** (a warning is allowed and is written down);
- it is **published**;
- it **does what the task says**: the observer starts a run with `2025Q1`, answers *approved*, and
  the rows are written; starts another, answers *rejected*, and the message is sent. The command
  `python scripts/exercise_setup.py check NAME` does both runs and prints the outcome and the state
  of every step of each; the observer reads it (it does not judge). A workflow that is valid but
  does something else does not pass;
- it has **five steps**, as the Proposal says. A solution with more steps that does the task is
  recorded as such and does not count as a pass for the number of steps.

NFR-06 is met if the people who took the exercise pass it. The Proposal does not say how many; this
file says: **at least two people, all of whom pass**. One pass in two is "not met", and what was
found goes in the retrospective.

## Record sheet

One sheet per person, filled in by the observer. Names are not written. The sheet is kept outside
the repository, and only the table of results below is copied in.

| Question | Answer |
| --- | --- |
| Person (P1, P2, ...) | |
| Role in the department, years with the data | |
| Has written a program or a formula language before (none, spreadsheet formulas, programming) | |
| Minutes of introduction | |
| Minutes from the start of writing to the first save | |
| Minutes to a save with no error | |
| Minutes to publication (or: not reached in 30) | |
| Number of saves | |
| Number of errors shown (codes) | |
| Times the observer was asked for help, and about what | |
| Does it do what the task says (approved, rejected) | |
| Number of steps | |
| Passed (yes / no) | |
| What the person said was hard, in their words | |
| What the observer saw that the person did not say | |

## Results

| Person | Passed | Minutes to publication | Observation |
| --- | --- | --- | --- |
| P1 | yes, at the limit | 30, as reported by the author, who was present; not timed to the minute | A statistician of the department, 3 years with data. Wrote the workflow himself, as the author tells it, and said that it was not difficult. Asked for help 3 times; what about was not written down. `exercise_setup.py check` on the published workflow: five steps; after *approved* the step that writes ran and the message step was skipped, after *rejected* the other way round; both runs `SUCCEEDED`. The workflow and its steps have the same names as the reference solution (`one_region_publication`, `load`, `check`, `approve`, `publish`, `return_to_region`); the task gives no names, and the example shown in the introduction has four of the five step names (`return_to_regions` for the last). Not recorded: the minutes to the first save and to a save with no error, the number of saves, the errors shown, what he found hard, and what the observer saw. |
| P2 | no | 47, as reported by the author, who was present; over the 30 | An analyst of the department; her role and her experience were not recorded. Finished the workflow, but after the 30 minutes. Asked for help 5 times; what about was not written down. She said it was a little hard for a first time and that she is sure it would be easier the next time. Whether her workflow does what the task says was not checked with `exercise_setup.py check`, and the number of its steps was not recorded; the verdict does not depend on it, since the time criterion is not met. Not recorded: the minutes to the first save and to a save with no error, the number of saves, the errors shown. |
| P3 | yes | 22, as reported to the author | A colleague of the department, 27 years old (role and experience with data not recorded). Asked for help 3 times; what about was not written down. Whether the published workflow was run with `exercise_setup.py check`, and its number of steps, were not recorded; the pass is the report that he managed it. Not recorded: the minutes to the first save and to a save with no error, the number of saves, the errors shown, what he found hard. |
| P4 | yes | 29, as reported to the author | A colleague of the department, 37 years old (role and experience with data not recorded). Asked for help 4 times; what about was not written down. Check and number of steps not recorded, as for P3. Not recorded: the minutes to the first save and to a save with no error, the number of saves, the errors shown, what he found hard. |

By the rule above (all who take the exercise pass), the result is written as it stands:
**NFR-06 is not met.** Three of the four passed (P1 at the limit, P3 in 22 minutes, P4 in 29) and one
did not (P2, 47 minutes). The times as reported are 30, 47, 22 and 29 minutes (median 29.5, mean 32),
and the requests for help are 3, 5, 3 and 4. The rule was written before the first run, and the two
later runs do not change it: a person who did not pass is not cancelled by two who did. Two things
can be said on the evidence: for a person who sees the language for the first time, 30 minutes after
a 15-minute introduction is tight (two of the four finished in 29 and 30 minutes, one in 22, one in
47), and every one of the four asked for help at least 3 times, so "without help" as the
requirement words it was not reached by anyone. A different rule (for example, a share of the people
who pass) would be a decision taken after seeing the results; if one is taken, it is written as
that, with the date, and this verdict stays beside it.

The record sheet must be filled in during the exercise: for P3 and P4, as for P1 and P2, the topics
of the requests for help, the errors shown and the minutes to the first save were not written down,
so this file cannot say what to change in the editor or in the introduction. The times are reports,
not measurements, and the date of the second pair is the day it was reported. The next run, if there
is one, records them.

What the first run showed about the exercise itself (not about the language): the set-up script
did not clear the data of an earlier start although this file said it did (the dry run had left its
users), a core of the dry run that was still running could answer in the place of the new one, and
the check said only "there is no workflow" for a wrong name. All three are fixed: the script clears the folder of an earlier start, refuses a port that
already answers (before it clears anything), and the check names the workflows that exist.

When the table is filled: copy the counts into the row of NFR-06 in `traceability.md`, replace
`not verified` by the result, and write what was learnt in the retrospective of the milestone in
which the exercise was run. If the result is a failure, it is written as a failure.
