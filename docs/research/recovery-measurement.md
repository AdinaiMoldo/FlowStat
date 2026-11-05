# Recovery and compensation against their baselines

Questions. After a crash, what does it cost to go on from the history instead of starting again,
and what does the rule for steps that may have taken effect (UNCERTAIN) buy and cost? Is the order
of a compensation that follows all dependencies worth more than the two obvious orders? What does
it cost to store the history before the commands run? The baselines are those of the card C4:
restart from scratch and naive reverse order.

Method: `python benchmarks/bench_recovery.py` and `python benchmarks/bench_wal.py`, fixed seed,
one machine, Python 3.13. Part A cuts the history of a run after every event, restores it and
finishes it on the simulated clock; the workloads are the nine-region example (approved and
rejected) and 40 random DAGs of 25 steps, a fifth of which send a mail (not idempotent), 3 workers.
Part B runs 200 random failing sagas (10, 20 and 30 steps, 60% undoable, 4 workers). The write cost
is measured on a database file on disk with the thread-pool driver.

Strategies in A:

- restart from scratch: the history is dropped and the run begins again;
- plain replay: the history is kept and every step that was RUNNING is run again, as if every
  action were idempotent (our restore with the rule for UNCERTAIN switched off);
- ours: the history is kept; a running step is repeated if its action is idempotent, otherwise it
  becomes UNCERTAIN.

A "repeated call" is a call of a step that had already been started before the crash. A "possible
duplicate effect" is a repeated call of an action that is not idempotent: its first call may have
taken effect before the crash. Calls of idempotent actions are not counted as duplicates because
the same key protects them (the run keeps its identifier).

## A. A crash after every event

Cells: repeated calls / of which the step had already finished / possible duplicate effects, summed
over all crash points of the workload.

| workload | crash points | restart from scratch | plain replay | ours | left to a person (ours) | time left: scratch | plain | ours |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| nine regions, approved | 45 | 550 / 432 / 0 | 117 / 0 / 0 | 117 / 0 / 0 | 0 | 100% | 69% | 69% |
| nine regions, rejected (mail) | 45 | 550 / 432 / 2 | 117 / 0 / 1 | 116 / 0 / 0 | 1 | 100% | 69% | 68% |
| 40 random DAGs of 25 steps (a fifth send mail) | 2040 | 27821 / 23179 / 5344 | 4642 / 0 / 898 | 3744 / 0 / 0 | 898 | 100% | 63% | 57% |

"Time left" is the time from the crash to the end of the run as a share of the uninterrupted run,
averaged over the crash points.

## B. The order of a compensation

| order | undo calls | undone too early | mean time of the compensation |
| --- | --- | --- | --- |
| reverse plan order, one at a time | 1466 | 0 | 16.5 (2.61x ours) |
| direct dependencies only | 1466 | 78 | 6.1 (0.96x ours) |
| ours | 1466 | 0 | 6.3 (1.00x ours) |

"Undone too early" counts undo calls that started before the undo of a step that depends on
the undone one (through any chain) had finished. The first order is correct and serial; the
second looks only at the steps that directly depend on a step and so misses a dependency that runs
through a step that has no `compensate`.

## C. The cost of writing the history first

The nine-region example, 46 events in 22 calls into the scheduler, median of 15 runs, time spent in
the store only (the actions return at once).

| history is stored | commits | events | time in the store | per commit |
| --- | --- | --- | --- | --- |
| before the commands, synchronous FULL | 22 | 46 | 19.2 ms | 0.87 ms |
| synchronous OFF | 22 | 46 | 2.0 ms | 0.09 ms |
| once, after the run | 1 | 46 | 1.1 ms | 1.12 ms |

## Conclusion

- Restarting from scratch repeats 12 calls per crash point on the example (550 over 45 crash points), 432 of them work that had already been done, and always takes the whole run again. Going on from the history repeats 2.6 calls per crash point (117 over 45), none of them finished work, and takes about two thirds of the run. On the random workloads restart repeats 27,821 calls and sends 5,344 possible second mails; going on from the history repeats 3,744 calls.
- The rule for UNCERTAIN removes every possible duplicate effect: plain replay repeats a mail whose first call may have worked 898 times over the 2,040 crash points of the random workloads and once over the 45 of the rejected example; ours repeats none and asks a person instead. That is the price: on the random workloads a person is asked 898 times over 2,040 crash points (0.44 per crash point), because a fifth of the steps send a mail and three run at once, so one is often in flight. In the Committee's example only one step in 22 is not idempotent, and only on the rejected path; the question was asked once over 45 crash points. A workflow with many non-idempotent steps gets many questions; that is the intended behaviour and not a defect, but it is a reason to give an action a key that its receiver honours.
- The smaller "time left" of ours on the random workloads (57% against 63%) is **not a gain**: a run that is waiting for a person stops there and its remaining time is not counted. It is shown to be complete in the table so that nobody reads it as speed.
- Compensation in reverse plan order is correct but 2.6 times slower, since it does one undo at a time. Looking only at direct dependencies is 4% faster than ours and wrong in 78 of 1,466 undo calls (5.3%): a step was undone while a step that used it was not yet undone. The saving comes from the constraints it ignores. Ours is correct and parallel.
- Writing the history before the commands costs 0.87 ms per call into the scheduler on this machine, 19 ms for the whole example, ten times the cost with `synchronous = OFF`: the time is the flush to disk. A run of a thousand steps would spend about a second in the store. Writing once after the run is cheaper but gives no recovery: a crash loses the whole history.

What these numbers do not show:

- The effects are modelled, not observed: a step "may have taken effect" as soon as it had started. Real actions differ; the exactly-once behaviour against a receiver is tested separately (`tests/crash/test_http_faults.py`).
- One machine and one disk. The flush to disk is the whole cost of the write-ahead log and depends on the disk; a server with a battery-backed cache would be faster, a slow disk slower. Two steps that finish together could share one commit (group commit); it is not done, since 19 ms per run does not call for it.
- The random workloads are not the Committee's workflows: a fifth non-idempotent steps is far more than in the example, and there are no waiting steps or retries.
- Compensation durations are drawn from one distribution; the times are virtual.
