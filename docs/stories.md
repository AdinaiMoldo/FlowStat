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


## M5

### US-35: As an operator, steps on the longest chain that can really run start first

Given a chain of two steps whose guards exclude each other, and a shorter chain that can run, when one worker is free, then the step that opens the shorter chain starts first. The plain longest path would start the other

Function C3 · 5 SP · Should

### US-36: As an operator, a run that was unfinished when a process stopped goes on when the process starts

Given a core or a service that stopped with runs unfinished (a step running, an approval waiting), when the process starts, then every run it owns is resumed from its history without a command: an idempotent step is run again with its key, a non-idempotent step that was running is UNCERTAIN, a waiting approval stays waiting. A run that is MIGRATING or MIGRATED_OUT is not resumed, and two starts do not drive a run twice

Function C4 · 5 SP · Should


### US-37: As an admin, the registry shows whether a deployed service answers

Given a deployed service whose process is killed, when the core probes it, then the registry says it does not answer within the probe interval; when it answers again, then the registry says running again. A probe never changes a deployment that was stopped on purpose

Function A6 · 3 SP · Should


### US-38: As a designer, I give an approval a time limit

Given a step that waits for a person and has `timeout: 3600`, when 3600 seconds of the run's clock pass without an answer, then the step fails with its own code and its `on_failure` applies. Given the process restarts after the limit, then the step still fails (the limit is measured from the recorded time of the wait). Given `timeout` on a step that does not wait, then the designer gets a diagnostic at save

Function C3 · 8 SP · Should


### US-39: As an admin, I limit the hosts that `http.call` may reach

Given a list of allowed hosts, when a step calls another host, then it fails permanently before anything is sent. Given a workflow whose constant URL names a host outside the list, then the designer gets a diagnostic at save. Given no list, then nothing changes

Function B6 · 3 SP · Should


### US-40: As an admin, I replace the key of a deployment without stopping it

Given a deployed service, when I rotate its control key (or its ingest key), then the new key works and the old one is refused after the switch. Given a stop in the middle of the rotation, then exactly one key works after restart

Function A3 · 5 SP · Should


### US-41: As a user of the API, listing runs does not compile a plan for each run

Given a published version and 100 runs of it, when the runs are listed, then the plan of the version is compiled once. The cost per run before and after is measured and written down

Function B4 · 3 SP · Could


### US-42: As an operator, the lists of runs and services update by themselves

Given the Runs page is open, when a run changes, then the table shows it within 5 seconds without a click, and the page asks no more while its tab is hidden

Function B2 · 2 SP · Could


### US-43: As a designer, a workflow of many guarded steps is analysed and not refused

Given a chain of 1,000 steps in which each step runs only if the one before it says so, when the draft is saved, then the checks finish within 3 seconds and report no internal error

Function C2 · 3 SP · Should


### DOC-7: As the author, I assemble the final report from the repository

Given the repository, when the check of the report runs, then every story and every NFR has a row in the traceability matrix with evidence or the reason it has none, the burndown and the velocity chart are made from the backlog, the coverage of each of the C1-C6 packages is at least 90%, and the negative check is filled

Function DOC · 5 SP · Must


### DOC-8: As a reader, I see the structure of the system in diagrams

Given the diagram sources, when they are rendered, then use case, context, class, component and deployment diagrams exist without error, and every class in the class diagram exists in the code

Function DOC · 5 SP · Must


### DOC-9: As a reader, I see the behaviour of the system in diagrams

Given the diagram sources, when they are rendered, then the state machines of step, run and ownership have exactly the states of the code, and each of C1 to C6 has an activity and a sequence diagram that follow its card

Function DOC · 8 SP · Must


### US-44: As an operator, I start a run and answer its approval on the page, and see the state of every step on the graph

Given a published workflow, when I press Run and give its inputs, then a run starts and the Runs view shows it; when a step waits for an approval, then Approve and Reject answer it and the run goes on if I hold the title the step names, and the core refuses and the page says why if I do not; and the graph of the run shows each step as succeeded, waiting, failed or skipped; and the button Example puts the example workflow in the editor

Function B2 · 5 SP · Should


### US-45: As a presenter, I sign in to the page as a role with one click, in a demonstration only

Given a core started for a demonstration on the loopback address, when I open the page, then there is a button for each role and a click signs in as that user; given a core started in any other way, then there is no such route and no button; and the core refuses to start with the option on any address but the loopback one

Function B2 · 3 SP · Could


### US-46: As a user, I sign in with my name and a password and get a session that ends

Given a user with a password, when I send my name and password, then I get a token that works as the token of that user, ends after 8 hours, at a sign-out and when the user is disabled; a wrong name, a wrong password and a user without a password give the same 401; after 5 failed attempts for a name within a minute the next gets 429 even with the right password; and the password is stored only as a salted hash

Function A3 · 8 SP · Should


### US-47: As a user, I sign in on a page of the system, move between its pages with working back and forward buttons, and start from a home page

Given the page, when I open it without being signed in, then I see a sign-in screen (a name and a password, and an API token as the other way in) and any other address sends me there and brings me back to it after I sign in; when I am signed in, each page has an address of its own, the back and forward buttons and a reload keep me where I am, the menu shows only the pages my role may use, and a session that the core ends sends me to sign in again; and the home page shows what the system does, how many workflows, runs and services there are, and the approvals that wait, each with a link to it

Function B2 · 8 SP · Should


### US-48: As a presenter, I sign in as colleagues with real first names and see the names written with a capital

Given a start of the demonstration, then the users are Adinai (admin), Azamat (designer), Alua (chief statistician), Atai and Marina (operators), each with a password of its own; and the page greets and shows a person by the name with a capital letter while the core keeps the lower case name

Function B2 · 3 SP · Could


### US-49: As an operator, I send the form of a region for a quarter to the core and see which regions have sent theirs

Given a region of the catalogue and a quarter like 2025Q1, when I send an xlsx file with the header code, name, value, then it is stored where the workflow reads it, replaces an earlier form, and the answer tells the rows and the statistical rules it breaks; a file that is not a form, a region that is not in the catalogue, a quarter of another shape and a body over 2,000,000 bytes are refused and nothing is stored; the listing tells for each region whether its form came; a form can be removed; and a designer may read the listing but not send

Function B2 · 8 SP · Should


### US-50: As an operator, I send the forms of the regions on a page, see which regions have sent theirs and which rules a form breaks, and then run the workflow on exactly those forms

Given the page Forms and a quarter, when I choose several Excel files named by the code of a region, then each goes to its region, the table shows for the nine regions whether the form came, its rows and the rules it breaks, and says whether enough forms came for the run (7 of 9); a file that is not a form, or a name that is no region, is told in words and changes nothing; I can replace or remove a form; a designer sees the table and has nothing to send with; sample sets of synthetic forms (good, six regions, with a mistake) are made by a script; and the run that follows reads exactly what was sent, so a form with a mistake stops it at its check and names the region

Function B2 · 8 SP · Should

### US-51: As a presenter, I show the whole cycle (sign in, send forms, check, a form that fails, approve) on pages that look finished, with no error in the console of the browser

Given a core with the example published, when an operator sends nine good forms, runs the workflow and the chief statistician approves, then every page shows the states with a badge for each state; when a form with a mistake is sent, the page of the run says in one block where it stopped (the step, the code and the message) and marks the step that failed; when only six forms are sent, it says that the goal was not reached and which steps failed; the page Forms says in colour whether enough good forms came and names the regions that break the rules, and has a button that removes all the forms of the quarter; a designer sees the forms and has nothing to send with; a role that may not change workflows has an editor that only runs them; Services, Migration and Statistics say what is missing when they have nothing to list, and Statistics shows the totals; and no page puts an error, a warning or a refused request into the console of the browser

Function B2 · 5 SP · Should

### US-52: As a presenter, I show pages that are never empty and a home page that gives each role what it has to do now, with the text and the look of a working system

Given a core started with a history, when any role signs in, then the home page has no paragraph that carries neither data nor an action, greets by name and role, and shows what that role has to do now: the designer the workflows with their version, state and runs and the buttons to write one or load the example, the operator how many of the nine regions have sent their forms and how many break the rules with a button to send them, the chief statistician the runs that wait with Review and Approve in the list, the administrator the runs of the week, how many failed and the latest runs; the counters give a context (how many this week, how many failed, how old the oldest waiting run is); one command (`exercise_setup.py start --logins --with-history`) makes the published workflow and a second version, nine runs over two weeks started by two operators (six reached the goal, one stopped at the check of BAT, one did not reach the goal, one waits for the approval and can be moved to the second version) and one service, so that no page of the menu is empty, while a start without the option stays empty; times are written as a person says them, run numbers are shortened to 8 characters with the whole one in the tooltip and copied by a click, each page has its own title of the tab, and no page puts an error or a warning into the console

Function B2 · 8 SP · Should

### DOC-10: As a reader, I see in one page how data would come from Tunduk, be checked on the platform and be published, and which parts exist

Given the repository, when I open `docs/target-scenario.md`, then the scenario (receive, check, approve, publish) is told in four steps, each step names the part of the system that does it and says whether it is done or not built, what is missing for Tunduk and for the website is listed with what it needs, and the page says that no part was tried against a real service

Function DOC · 2 SP · Could

### US-53: As an administrator, I create a reporting campaign for a quarter and move it through a life cycle in which only the allowed changes can be made

Given the catalogue and a workflow, when an administrator creates a campaign with a name, a year, a quarter, the regions, a deadline, the workflow and its version and thresholds of completeness, then it is stored as a DRAFT, wrong settings are refused with the field that is wrong, and a name is used once; a draft can be edited and a campaign that has left DRAFT cannot; it opens only when its workflow version is published; a person can make only the changes the table of the life cycle gives to a person (open, start collecting, close collecting and validate, collect again, finalize, archive), and a change that follows from a consolidation or an approval, or is not in the table, is refused with its own code; every role can read the campaigns and only an administrator can change them, checked on the server; the page Campaigns lists them with the forms that have come and the days to the deadline, shows for one campaign how many forms are received, valid, with errors and missing, which region is which, whether there are enough valid forms for a preliminary and for the final result, and offers only the changes the server allows; and the forms of a quarter whose campaign is approved, finalized or archived cannot be replaced or removed, while a quarter with no campaign is as free as before

Function A1 · 8 SP · Must

### US-54: As a statistician of the committee, I have the content of every regional form checked by rules of arithmetic, range, relation and completeness, and each violation tells me where it is and why

Given a set of rules for a template, when a form is checked, then every finding has a stable code, a severity (error, warning, info), the region, the file, the sheet, the cell or the indicator, the value found and the condition expected; only an error blocks the form, a warning and an info are told to the sender; an empty cell, a zero, a missing indicator and "n/a" are four different results; the arithmetic is exact (a total of 0.6 for 0.1, 0.2 and 0.3 holds, a total of 0.61 is off by exactly 0.01); a part more than its whole, a value out of its range and a total that is not the sum of its parts are errors that name the cell; the basic rules and their words stay as they were for every form and every workflow that does not ask for more; a campaign chooses the set of rules for the forms of its quarter, an unknown set is refused with the field, and the progress counts errors and warnings apart; the page Forms shows each finding with its code and, on hover, what was found and what was expected; and the step statguard.validate takes the set as an optional argument

Function B1 · 8 SP · Must

### DEF-1: As a developer, I get a test run that does not die at random, so that a green main means the tests passed and not that the crash did not come

Given a connection of a request that is closed while another thread is still inside a call on it, when the process runs, then it does not crash: a call that is under way finishes before the connection closes, and a call after it gets the ordinary error that the database is closed; the crash is reproduced in a process of its own (it killed seven of eight runs before the change); a connection behaves as a sqlite3 connection in every other respect (execute, executemany, executescript, cursors, fetch, rollback, double close); no test is skipped and no job is run again to hide it

Function B2 · 5 SP · Must
### US-55: As an operator, I send the form of my region again after a correction and both versions are kept with their results, so that I and the chief statistician can see which errors are gone

Given a region that sends its form twice, when each file is accepted, then each is a version with who sent it, when, the set of rules that was applied and the findings of that time; the current version is the latest one that was not removed and it is the file that a run reads; a file that is sent again unchanged is not a new version; a file that cannot be read, or a form of a quarter whose campaign is approved, finalized or archived, makes no version; removing a form keeps its history and a form sent later continues the numbers; a file that was put on the disk by hand, or changed after it was sent, is not shown as a version; every role can read the history and the comparison of two versions, which tells what the newer fixed, what remains and what is new (the same mistake in another cell is the same mistake); and the page Forms shows the version of each region and, on request, its versions and what the newest changed

Function A2 · 8 SP · Must

### US-56: As an operator, I see each error of a form I sent as a problem with someone who works on it, so that a mistake is corrected, discussed and checked again instead of lost in a list

Given a new version of a form with errors, when it is kept, then one problem is opened for each mistake (the same code for the same indicator or cell), assigned to the user who sent it, with the cell, the message and the found and expected values, and the history says who and when; warnings open nothing, and the same file sent again changes nothing; a user who may write (admin, operator) gives a problem to a user who exists and is not disabled, or takes it away, and adds a comment of up to 2,000 characters; a resolved problem cannot be assigned but can be commented; every role reads the problems with filters (quarter, region, status, assignee, only those to work on); when a corrected version comes, a problem whose mistake is gone goes to revalidating and then to resolved, a problem whose mistake is still there (also in another cell) goes to revalidating and then to reopened with the new cell, and a resolved problem whose mistake returns is reopened, each change as an event of the system with the number of the version; and the page Problems lists them, shows the history of one, and lets an operator or an administrator comment, assign, and assign to themselves, while a designer only reads

Function B2 · 8 SP · Must

### US-57: As a statistician of the committee, I make a preliminary and a final result of a campaign that says which regions are in it and why the others are not, so that an incomplete total is never read as the total of the country

Given a campaign that is being validated, when enough valid forms are there for a preliminary result (`preliminary_min`), then a result is made that lists the regions included with the version and the hash of the form of each, the regions left out with the reason (missing, or invalid with the number of errors), the share, who made it and when, and is marked incomplete unless every region is in it; a final result needs the policy of the campaign (`final_min`) and is refused with the regions that are not in otherwise; a refused result keeps nothing and leaves the campaign where it was; every total is an exact decimal and carries the figures it was added from (region, form version, value); each result has a number and is never changed; a form of a region of the campaign that is sent again or removed marks the results stale with the reason and sends a campaign whose result was shown back to collecting, and the next result is a new number; a preliminary result moves the campaign to PRELIMINARY and a final one to PENDING_APPROVAL; an admin or an operator makes a result and every role reads them; the page Campaigns makes a result, lists them with complete, incomplete and stale, and shows where each total comes from

Function A5 · 13 SP · Must
