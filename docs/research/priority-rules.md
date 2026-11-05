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
