# The predicate solver and the test generator against their baselines

Questions. Is the solver, with its clause cap, faster than looking at every assignment, and how
often does the cap make it give up? Does the generator produce fewer cases than writing one case
per target, and more coverage than random inputs? The baselines are those of the card C6: brute
force and random inputs.

Method: `python benchmarks/bench_solver.py`, fixed seed, one machine, Python 3.13, about 15 s.
Part A draws 200 random predicates for each size (2 to 32 atoms: comparisons of a variable with a
constant, differences of two variables, joined by and, or, not) over four integer variables in
0..9 and asks "is there an assignment?" of the solver and of brute force (10,000 assignments, each
checked with the evaluator of the predicate). Part B generates tests for 300 random workflows of
2 to 7 steps with every join rule and guards on four declared inputs (an integer 0..100, a
three-valued enumeration, a real 0..1, a boolean); 294 have at least one feasible target.

## A. Solver against brute force

| atoms | cap | solver (ms) | brute force (ms) | speed-up | answered | wrong |
| --- | --- | --- | --- | --- | --- | --- |
| 2 | 64 | 0.051 | 3.6 | 69x | 100% | 0 |
| 4 | 64 | 0.087 | 4.7 | 55x | 100% | 0 |
| 8 | 64 | 0.175 | 10.1 | 58x | 100% | 0 |
| 16 | 64 | 0.467 | 11.6 | 25x | 94% | 0 |
| 16 | 4096 | 0.630 | 11.6 | 18x | 100% | 0 |
| 32 | 64 | 0.672 | 23.9 | 36x | 36% | 0 |
| 32 | 4096 | 10.191 | 23.9 | 2x | 100% | 0 |

(The cap 4096 rows for 2, 4 and 8 atoms are identical to those of the cap 64: no predicate
needed more than 64 clauses.) The solver never disagrees with brute force when it answers. Up to
8 atoms it answers everything, 55 to 70 times faster than looking at the grid; the brute-force
time of this table is also small because four variables in 0..9 are few, and it grows ten times
with every variable added while the solver does not depend on the size of the domain. At 16 atoms
6% of the predicates need more than 64 clauses, at 32 atoms 64%: the cap turns those into
"unknown", which the analysis reports as a note and never as a warning. Raising the cap answers
them all but at 32 atoms costs 10 ms and is only twice as fast as the grid; this is where the DNF
is exponential, and it is the reason for the cap. Real guards have two to four atoms.

## B. Generator against one case per target and random inputs

| | cases per workflow |
| --- | --- |
| one case per feasible target (naive) | 6.15 |
| ours (witnesses, boundary neighbours, exact cover) | 2.04 |
| random inputs, until the targets of ours are covered | 52.96 |

- Ours covers 100% of the feasible targets of every workflow; no workflow had a target that was
  neither covered nor proved infeasible.
- Random inputs, as many as ours, cover 68.7% of those targets. They need 53 cases on average to
  reach what ours reaches, because a guard such as `amount == 50` is true for one input in 101
  and `amount == 0` or `ratio >= 0.75` at a bound is hit rarely; the solver and the neighbours
  of the constants find those at once.
- Three times fewer cases than one per target, because one case settles several independent
  guards, and because a target is decided by what runs and not by the guard alone.

## Conclusion

The solver is exact where it answers, fast for the guards that occur (a handful of atoms), and
honest about the rest: the cap costs nothing for guards of 8 atoms or fewer and turns the large
ones into "unknown". The generator gives a set a third the size of one case per target and
covers what random inputs miss. Not measured: guards with strings of a pattern (the generator
does not produce them), instances of a `foreach` (not targets), predicates over many more
variables than four. The numbers are from one machine and one distribution of workflows.
