# Availability on a long chain of guarded steps

Question: does the analysis of a draft (types, guards, availability of outputs, priorities of the
scheduler) still finish, and finish in the time of NFR-01 (a file of 5,000 lines in 3 s), when the
workflow is one long chain in which each step runs only if the one before it says so?

How it was found: the chain of `benchmarks/bench_compile.py` is compiled in a fraction of a second
per 1,000 steps, but that benchmark never calls the analyses of M5; handing the same shape to them
for the NFR-01 row of the report gave the error.

Method: `python benchmarks/bench_chain.py`, Python 3.13, default recursion limit. Step i needs
step i-1 and has `when: "${s(i-1).ok} == true"`, five lines per step. The time is that of
`availability.analyse` and `critical.priorities` together, after compiling. "Without the bound"
sets `runs.RUN_CAP` to 10^9, which is the behaviour before US-43.

| steps | lines | without the bound | with the bound (RUN_CAP = 200) |
| --- | --- | --- | --- |
| 100 | 500 | 0.08 s | 0.08 s |
| 250 | 1,250 | 0.75 s | 0.22 s |
| 500 | 2,500 | RecursionError | 0.68 s |
| 1000 | 5,000 | RecursionError | 1.86 s |
| 2000 | 10,000 | RecursionError | 5.80 s |

Cause: the run condition of a step is the conjunction of its guard and the run condition of the
step it needs, so the condition of step n holds n guards nested n deep. `bounds` and `nnf` recurse
on it, so a chain of about 500 steps exhausted the stack; a draft of that size gave an internal
error where the API saves it. Before that the time grew with the square of the length (the sizes
of the conditions add up).

Change: a run condition of more than 200 nodes is given up (an opaque atom, "may or may not
run"), so the nesting is bounded and every later step of the chain starts again from a small
condition. The size is counted by a loop with a stack (`runs.within`), which stops at the bound.
The bound was chosen by trying it: at 500 the 1,000-step chain was analysed but 1,000 failed in
`nnf` with the same error, since the depth of the recursion is the bound divided by two, times the
frames of one level.

What it costs: a chain is exact for about 100 guarded steps in a row (two nodes per step with a
one-atom guard). After that the steps lose the facts that depend on the whole chain. A read of an
output behind them gets a warning that a more exact analysis would not give; no read that is unsafe
is let through. The soundness test of C2 was run again with the bound set to 3, so that conditions
are given up often (`test_giving_a_condition_up_never_makes_the_analysis_promise_too_much`: in the
three seeds 38, 33 and 29 conditions of 80 random workflows each were given up, and 720, 729 and 461
facts were checked against the reference interpreter, none wrong).

Limits: the time with the bound is still above linear (1.9 s for 1,000 steps, 5.8 s for 2,000, about
n^1.6), which I did not remove: the 5,000-line requirement is met, 10,000 lines is not. One shape of
chain was measured, the one a guard on the previous output gives; a chain through `any_success` or
`at_least` joins goes through the quorum cap of 64 and was not measured separately.
