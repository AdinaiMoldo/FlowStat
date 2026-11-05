# Priority of ready steps: FIFO against the plain longest path

Question: when more steps are ready than there are free workers, which ones start first? M2 ships
the plain longest path (a step's priority is its duration plus the longest chain behind it). Is it
worth more than the obvious alternative, first in first out? The answer is the baseline that the
feasible critical path of M5 has to beat (C3, level 2).

Method: `python benchmarks/bench_priority.py`, fixed seed, one machine, Python 3.13. For each size
and each number of workers 100 random DAGs are generated: every step needs 0 to 3 of the 12 steps
before it, durations are 1 plus an exponential with mean 3, and one step in ten is six times longer.
Each DAG runs on the simulated clock with both policies, with the true durations known to the
priority. The makespan is divided by a lower bound, the larger of the longest chain and the total
work divided by the number of workers; no schedule can reach below 1.000.

| steps | workers | FIFO / bound | longest path / bound | makespan saved | better / equal / worse |
| --- | --- | --- | --- | --- | --- |
| 50 | 2 | 1.077 | 1.004 | 6.4% | 98 / 0 / 2 |
| 50 | 4 | 1.123 | 1.004 | 10.1% | 96 / 4 / 0 |
| 50 | 8 | 1.012 | 1.000 | 1.1% | 41 / 59 / 0 |
| 200 | 2 | 1.045 | 1.000 | 4.1% | 99 / 0 / 1 |
| 200 | 4 | 1.215 | 1.002 | 17.1% | 100 / 0 / 0 |
| 200 | 8 | 1.130 | 1.001 | 11.0% | 100 / 0 / 0 |

Conclusion:

- The plain longest path is within 0.4% of the lower bound on these workflows. FIFO is up to 21.5% above it (200 steps, 4 workers), so the priority is worth having; it saves up to 17% of the running time (10% to 17% with 4 workers).
- It is not always better: in 3 of 600 cases FIFO was shorter. Priority by longest chain is a heuristic (the problem is NP-hard with a limited number of workers), not a guarantee.
- With many workers (8 on 50 steps) there is rarely a queue, so the policy hardly matters.
- A small case that fails with FIFO is kept as a test (`test_longest_path_beats_fifo_when_a_short_step_opens_a_long_tail`): a short step opens a tail of 10; FIFO starts it after two unrelated steps and ends at 12, the longest path starts it at once and ends at 11.

What this does not show: the generated DAGs have no `when` conditions and no join rules other than
`all_success`, so every chain is really executed. In the workflows of the Committee a long chain
behind a condition may never run, and the plain priority counts it anyway. That is the case the
feasible path of M5 is meant for. M5 must therefore (a) use a generator with guarded branches and
(b) report against this table. Whether the feasible path wins there is not yet known and is not
claimed.

Limits: one machine; durations are drawn from one distribution; the priority knows the true
durations, whereas a real run only has averages from earlier runs (default 1).


# The feasible critical path against the plain longest path (M5)

Question: does the feasible critical path (`analysis/critical.py`, dsl-spec 11) shorten the run
more than the plain longest path, on workflows where a long chain may sit behind conditions that
exclude each other? The note above said this was not yet known and was not claimed.

Method: `python benchmarks/bench_critical.py`, fixed seed, one machine, Python 3.13. The DAGs are
those of the table above with two additions: a share of the steps has a guard on one integer input
(`amount > t` or `amount <= t`), and 40% of the steps with dependencies use `all_done`, so that a
chain can pass over a skipped step. Workload A: 40% guarded, a tenth of the steps 6 times longer.
Workload B: 70% guarded, a fifth of the steps 10 times longer. Each of 100 workflows runs for ten
random values of the input (1,000 runs per row). The makespan is divided by a bound: the larger of
the longest chain that really runs and the work of the steps that run divided among the workers.
"All inputs" is the feasible path with the input unknown; "this run" is the one the run service
computes, with the value of the input put into the guards. Durations are the true ones.

Workload A (makespan / bound):

| steps | workers | FIFO | plain | feasible, all inputs | feasible, this run | this run vs plain: better / equal / worse | saved |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 50 | 2 | 1.093 | 1.006 | 1.005 | 1.005 | 451 / 117 / 432 | 0.07% |
| 50 | 4 | 1.110 | 1.009 | 1.008 | 1.004 | 197 / 742 / 61 | 0.47% |
| 50 | 8 | 1.008 | 1.000 | 1.000 | 1.000 | 9 / 988 / 3 | 0.00% |
| 200 | 2 | 1.059 | 1.001 | 1.001 | 1.001 | 536 / 0 / 464 | 0.01% |
| 200 | 4 | 1.222 | 1.006 | 1.005 | 1.003 | 532 / 117 / 351 | 0.24% |
| 200 | 8 | 1.118 | 1.003 | 1.002 | 1.001 | 269 / 671 / 60 | 0.26% |

Workload B:

| steps | workers | FIFO | plain | feasible, all inputs | feasible, this run | this run vs plain: better / equal / worse | saved |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 50 | 2 | 1.104 | 1.014 | 1.010 | 1.008 | 441 / 283 / 276 | 0.50% |
| 50 | 4 | 1.060 | 1.008 | 1.004 | 1.002 | 138 / 840 / 22 | 0.54% |
| 50 | 8 | 1.004 | 1.000 | 1.000 | 1.000 | 5 / 995 / 0 | 0.01% |
| 200 | 2 | 1.082 | 1.001 | 1.001 | 1.001 | 559 / 0 / 441 | 0.01% |
| 200 | 4 | 1.208 | 1.013 | 1.008 | 1.003 | 525 / 245 / 230 | 0.87% |
| 200 | 8 | 1.072 | 1.004 | 1.002 | 1.001 | 198 / 776 / 26 | 0.29% |

Cost of computing the priorities (mean, maximum): with the inputs of the run, 0.5 ms (2 ms) for 50
steps and 3 to 4 ms (10 ms) for 200 steps; with the input unknown, 26 ms (293 ms) for 50 steps and
314 to 348 ms (about 1.1 s) for 200 steps. The cap of 16 chains was reached in 97 to 100 of every
100 workflows when the input is unknown.

Conclusion:

- With the inputs of the run, the feasible path is closer to the bound than the plain path in
  eight of the twelve rows and equal to three decimals in the other four, and it is never above
  it on average. The gain is small: 0.0% to 0.9% of the makespan (largest: workload B, 200 steps,
  4 workers, 0.87%). The plain path was already within 1.4% of the bound, so there is little
  room; in the row with the largest gap the feasible path closes most of it (1.013 to 1.003).
- It is a heuristic: in the same rows a run is longer than with the plain path in up to 464 of
  1,000 cases (where the totals are close, the order of two equal priorities decides), and the
  table is a mean. It is not claimed to be better in a given run.
- Without the inputs (the reading of the Proposal), the result is at most the plain path in the
  mean, and the cap binds almost always, so most of the pruning is lost; the cost is 50 to 100 times
  that of the run's own inputs. The service therefore computes it for the run.
- The first version dropped the chains beyond the cap, which made a priority too short. On
  workload A with 200 steps and 4 workers, no inputs, an earlier run of the same generator gave
  1.088 of the bound against 1.006 for the plain path. A cut now keeps the longest chain that was left out, and the result is
  1.005. This measurement is why the priorities are upper bounds.
- Where it matters most is the case of the story US-35 (a long chain whose guards exclude each
  other against a shorter one that can run): there the plain path starts the wrong step and the
  feasible one starts the right one (`tests/acceptance/test_us35.py`). The random workflows have
  few such chains, which is why the mean gain is small.

Limits: one machine; one integer input with thresholds drawn at random, not the guards of the
Committee's workflows; durations are drawn from one distribution and known to the priority,
whereas a real run has only averages from earlier runs (the default is 1); failures and waiting
steps are not modelled; the workers are identical.
