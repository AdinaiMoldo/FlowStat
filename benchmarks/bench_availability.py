"""Availability of outputs: our analysis against plain reachability, checked by enumeration (C2).

Run: python benchmarks/bench_availability.py

For random workflows (guards on the declared inputs, every join rule, steps with
``on_failure: continue``) every pair "Y reads the output of X" with X an ancestor of Y is classified
by the reference interpreter, which runs the workflow for every combination of the values that the
guards can tell apart and of the steps that may fail:

* safe: whenever Y starts, X has succeeded (a warning on this reference would be a false alarm);
* unsafe: there is an execution in which Y starts and X has not succeeded (a warning is right).

Two analyses answer "may Y read X without a warning?":

* plain: X is a mandatory ancestor of Y, that is, reachable through ``all_success`` joins, and for
  the other joins only what all the dependencies have in common (the intersection of sets);
* ours: ``analysis.availability.available_at`` (run conditions and the solver, dsl-spec 10).

The table counts, for each, the safe pairs it recognises (the others get a false warning) and the
unsafe pairs it lets through (which must be none). The conclusion is kept in
docs/research/availability-measurement.md.
"""

from __future__ import annotations

import random
import sys
import time
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.reference.interp import SUCCEEDED, ancestors, executions, may_fail
from tests.unit.analysis.helpers import SIGNATURES, random_workflow

from flowstat.analysis.availability import available_at
from flowstat.analysis.variables import lower_guards
from flowstat.compiler.plan import Plan
from flowstat.solver.predicate import variables

SEED = 20261105
WORKFLOWS = 250
GRID: dict[str, list[object]] = {
    "inputs.amount": [0, 25, 26, 50, 51, 75, 76, 100],
    "inputs.mode": ["fast", "slow", "off"],
    "inputs.ratio": [0.0, 0.25, 0.5, 0.75, 1.0],
    "inputs.flag": [True, False],
}


def plain_available(plan: Plan) -> dict[str, set[str]]:
    """The mandatory ancestors: what every execution in which the step starts has run before."""
    out: dict[str, set[str]] = {}
    for step in plan.steps:
        sets = [{d} | out[d] for d in step.needs]
        kind = step.join.kind
        if not sets or kind == "all_done":
            out[step.id] = set()
        elif kind == "all_success" or (kind == "at_least" and step.join.k == len(sets)):
            out[step.id] = set().union(*sets)
        else:  # any_success, at_least k: only what every dependency has behind it
            out[step.id] = set.intersection(*[out[d] for d in step.needs])
    return out


def safe_pairs(plan: Plan) -> dict[tuple[str, str], bool]:
    """(Y, X) -> X succeeded in every execution in which Y started; pairs where Y never starts
    are left out."""
    used = set()
    for p in lower_guards(plan, SIGNATURES).preds.values():
        used |= variables(p)
    grid = {name: values for name, values in GRID.items() if name in used}
    above = ancestors(plan)
    safe: dict[tuple[str, str], bool] = {}
    for e in executions(plan, grid):
        for y, state in e.states.items():
            if state != SUCCEEDED and not (state == "FAILED" and y in e.failing):
                continue
            for x in above[y]:
                safe[(y, x)] = safe.get((y, x), True) and e.states[x] == SUCCEEDED
    return safe


def main() -> None:
    rng = random.Random(SEED)
    pairs = recognised_ours = recognised_plain = unsafe = wrong_ours = wrong_plain = 0
    seen = used = 0
    ours_time = 0.0
    while seen < WORKFLOWS:
        plan = random_workflow(rng, guard_chance=0.6, continue_chance=0.35)
        if len(may_fail(plan)) > 4:
            continue
        seen += 1
        truth = safe_pairs(plan)
        plain = plain_available(plan)
        started = time.perf_counter()
        ours = {y: available_at(plan, SIGNATURES, y) for y in {y for y, _ in truth}}
        ours_time += time.perf_counter() - started
        used += bool(truth)
        for (y, x), safe in truth.items():
            pairs += 1
            if safe:
                recognised_ours += x in ours[y]
                recognised_plain += x in plain[y]
            else:
                unsafe += 1
                wrong_ours += x in ours[y]
                wrong_plain += x in plain[y]
    safe_count = pairs - unsafe
    print(f"{seen} random workflows, {pairs} pairs (Y, X) where X is above Y and Y can start")
    print(f"  safe {safe_count}, unsafe {unsafe}")
    print("| analysis | safe pairs recognised | false warnings | unsafe pairs let through |")
    print("| --- | --- | --- | --- |")
    for name, ok, wrong in (
        ("plain (mandatory ancestors)", recognised_plain, wrong_plain),
        ("ours (run conditions and solver)", recognised_ours, wrong_ours),
    ):
        print(
            f"| {name} | {ok} ({ok / safe_count:.1%}) | {safe_count - ok} "
            f"({1 - ok / safe_count:.1%}) | {wrong} |"
        )
    removed = recognised_ours - recognised_plain
    left = safe_count - recognised_plain
    print(
        f"false warnings removed: {removed} of the {left} that plain reachability gives "
        f"({Fraction(removed, left) * 100:.1f}%)"
    )
    print(f"time of the analysis: {1000 * ours_time / seen:.1f} ms per workflow of 2 to 7 steps")


if __name__ == "__main__":
    main()
