# M8 measurement: the check of a migration, the handover and the statistics

Script: `benchmarks/bench_migration.py` (`python benchmarks/bench_migration.py`). Machine: the
development sandbox (one Linux container, Python 3.13). Limits: the stores are in-memory SQLite
databases and the calls between owners are function calls in one process, so the figures are the work
of the protocol and not of a network; the routes between the core and a service were tested for
correctness (`tests/acceptance/test_us33.py`, two real processes) but not timed.

## The check (C5, level 1)

The nine-region example is run on the simulated clock and the check is made for the history cut after
every event, which is every moment at which a person might ask. The plans are compiled once, as the
service does when it lists many runs.

| Check | Histories | median ms | p95 ms | max ms |
| --- | --- | --- | --- | --- |
| same version (33 events, 7 migratable) | 33 | 0.120 | 0.170 | 0.236 |
| adds a step (33 events, 7 migratable) | 33 | 0.116 | 0.165 | 0.185 |

## The handover (C5, level 2) against stop and restart

A run that waits for an approval is moved between the core and two services. Every call of the
protocol is a crash point; the process is stopped before and after it, and once more at every call of
the recovery. After the recovery the stores are asked who owns the run and whether the history is
whole ("sound": exactly one owner, the history of the run equal to the original plus one `RUN_MIGRATED`,
and the old owner refuses commands). The last column is a move with no fault.

| Move | one crash | sound | two crashes | sound | median ms |
| --- | --- | --- | --- | --- | --- |
| core -> svc1 | 14 | 14/14 | 156 | 156/156 | 0.339 |
| svc1 -> core | 14 | 14/14 | 156 | 156/156 | 0.303 |
| svc1 -> svc2 | 14 | 14/14 | 156 | 156/156 | 0.280 |

The baseline of the card, stopping the run and starting it again under the new version:

| Method | runs kept | steps finished before | steps done again |
| --- | --- | --- | --- |
| handover | 1/1 | 1 | 0 |
| stop and restart | 0/1 | 1 | 2 |

## Statistics

| Statistics | Runs | median ms | p95 ms | max ms |
| --- | --- | --- | --- | --- |
| counts of 1,000 runs, 33 events each, 3 workflows | 1000 | 3.8 | 3.8 | 3.9 |

This is the counting alone, from events already in memory; reading 1,000 histories from SQLite comes
before it and was not timed here.

## Conclusions

- **The check is cheap enough to run for every unfinished run on every request.** About 0.12 ms for a
  history of up to 33 events, linear in the events plus the size of the plan; a listing of a thousand
  runs costs about a tenth of a second plus compiling the two versions once. No cache is needed.
- **The check does not depend on the change.** Adding a step costs the same as comparing a version with
  itself (0.116 against 0.120 ms): the work is the one fold of the history and one evaluation of each
  guard and read.
- **The handover is sound at every crash point that was tried.** All 14 single crashes and all 156
  double crashes (a second stop during the recovery) of each of the three topologies ended with one
  owner and a whole history. This is a measurement over the points the protocol has, and says nothing
  of a fault inside a SQLite commit, which is the database's guarantee and not ours.
- **A move costs about 0.3 ms in-process.** Over HTTP it is five requests and the time of the network
  and of the transaction at each store; those were not timed. The cost that matters to a person is
  not the move but the wait for a step that is running (the fence is refused with `E-MIG-002`
  while a step runs, and the person tries again).
- **Handover keeps the run; stop and restart does not.** Starting again gives a new run and does the
  finished steps again (two steps in the example, which include a mail). The only argument for
  stopping and restarting is that it needs no protocol; this run is the case it cannot serve.
- **The statistics are a pass over the events and cost a few milliseconds per thousand runs.** The
  expensive part for a large store would be reading the histories, not counting them. The page
  reads all runs on every refresh; a stored summary per run would be the change if that shows (not
  needed at this size).
