# Availability of outputs: our analysis against plain reachability

Question: how many false warnings does the availability analysis of M5 (C2, dsl-spec 10) remove
compared with the plain analysis that a designer's tool would otherwise use, and does it ever let
a read of a missing output through? The baseline named in the Proposal is the analysis without
conditions.

Method: `python benchmarks/bench_availability.py`, fixed seed, Python 3.13. 250 random workflows of
2 to 7 steps (every join rule, guards on the declared inputs, 35% of the steps with
`on_failure: continue`, at most four of them). For each pair (Y, X) with X an ancestor of Y, the
reference interpreter (`tests/reference`, which shares no code with the analysis) runs the
workflow for every combination of the input values that the guards can tell apart (up to 240) and
of the steps that may fail (up to 16). A pair is *safe* when, in every execution in which Y
starts, X has succeeded; a warning on a safe pair is a false alarm. A pair is *unsafe* when some
execution starts Y without X having succeeded.

* plain: X is a mandatory ancestor of Y, that is, reached through `all_success` joins; for
  `any_success` and `at_least k` only what every dependency has in common (the intersection of
  sets); nothing through `all_done`. No conditions.
* ours: `available_at`, the facts of 10.1 and an entailment query to the solver (10.2).

| analysis | safe pairs recognised | false warnings | unsafe pairs let through |
| --- | --- | --- | --- |
| plain (mandatory ancestors) | 370 of 648 (57.1%) | 278 (42.9%) | 0 |
| ours (run conditions and solver) | 593 of 648 (91.5%) | 55 (8.5%) | 0 |

1,058 pairs, 648 safe and 410 unsafe. Our analysis removes 223 of the 278 false warnings of the
plain one (80.2%) and lets no unsafe pair through, which is the soundness that the oracle test
checks on the same kind of workflow (`tests/unit/analysis/test_availability.py`). The plain
analysis is also sound here, as it should be: it only claims what is mandatory.

Time of the availability analysis alone: 0.4 ms for the example workflow (22 steps), 4 ms on average for a random workflow of 50 steps and
42 ms (at most 102 ms) for one of 200 steps, so the check on every save of a draft is not felt.

What the remaining 55 false warnings are: 47 are reads behind an `at_least` join, 6 behind
`any_success`, 1 each behind `all_done` and `all_success`. The typical case is a step that needs
two dependencies of which one depends on the other (`s2` needs `s1` and `s0`, `s1` needs `s0`, and
`s0` may fail with `continue`): when `s2` starts, one of them succeeded, and in both cases `s1`
did, but the rule only passes on facts about the ancestors of each dependency, so it cannot show
that `s1` followed from `s0`. The warning is then a message about something that is safe; it is
never the other way round.

Limits: random workflows of up to seven steps and one kind of guard, not the Committee's
workflows (the example gives no finding); the grid contains the values at both sides of every
threshold in the guards but a guard with a threshold outside it would be missed by both the
interpreter and the test; failures are modelled only for steps with `continue`, as the
specification says a failure of any other step stops the run; the analysis is a sufficient
condition, so the figure for false warnings is a measure of precision and not of correctness.
