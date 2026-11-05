"""The predicate solver and the test generator against their baselines (C6, M4).

Run: python benchmarks/bench_solver.py

Part A asks the same question, "is there an assignment for which the predicate holds?", of the
solver and of brute force, for random predicates of 2 to 32 atoms over integer variables in 0..9
(comparisons, differences of two variables, and, or, not). It reports the time of each, how often
the solver commits to an answer, and whether any answer disagrees with brute force. The clause cap
(64 in the product) is varied, since it is the one place where the solver gives up.

Part B generates tests for random workflows (every join rule, guards on the declared inputs) and
compares three ways to cover the targets "step runs" and "step is skipped":

* naive: one case per feasible target (what a person would write by hand);
* random: uniform random inputs, as many as ours, then as many as it takes to equal ours;
* ours: the generator (witnesses, boundary neighbours, exact set cover).

The workflows are generated from a fixed seed. The conclusion is kept in
docs/research/solver-measurement.md.
"""

from __future__ import annotations

import itertools
import random
import statistics
import sys
import time
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.unit.analysis.helpers import SIGNATURES, random_workflow

from flowstat.analysis.generate import Target, generate
from flowstat.analysis.predict import RUN, SKIP, predict
from flowstat.analysis.variables import lower_guards
from flowstat.solver.domains import Domain
from flowstat.solver.predicate import And, Cmp, Diff, Not, Or, Pred, holds
from flowstat.solver.solve import Status, check

SEED = 20261105
NAMES = ("a", "b", "c", "d")
DOMAIN = Domain.integer(0, 9)
OPS = ("<", "<=", ">", ">=", "==", "!=")


# -- part A: solver against brute force --------------------------------------------------------
def random_atom(rng: random.Random) -> Pred:
    if rng.random() < 0.7:
        return Cmp(rng.choice(NAMES), rng.choice(OPS), Fraction(rng.randint(-1, 10)))
    x, y = rng.sample(NAMES, 2)
    return Diff(x, y, rng.choice(OPS), Fraction(rng.randint(-5, 5)))


def random_predicate(rng: random.Random, atoms: int) -> Pred:
    if atoms == 1:
        atom = random_atom(rng)
        return Not(atom) if rng.random() < 0.2 else atom
    cut = rng.randint(1, atoms - 1)
    left, right = random_predicate(rng, cut), random_predicate(rng, atoms - cut)
    return (And if rng.random() < 0.5 else Or)((left, right))


GRID = [dict(zip(NAMES, values, strict=True)) for values in itertools.product(range(10), repeat=4)]


def brute_force(p: Pred) -> bool:
    return any(holds(p, env) is True for env in GRID)


def part_a(rng: random.Random) -> None:
    print("A. solver against brute force (4 variables in 0..9, 10,000 assignments, 200 predicates)")
    print(
        f"{'atoms':>5} {'cap':>5} {'solver ms':>10} {'brute ms':>9} {'speed-up':>9} "
        f"{'decided':>8} {'wrong':>6}"
    )
    domains = {name: DOMAIN for name in NAMES}
    for atoms in (2, 4, 8, 16, 32):
        preds = [random_predicate(rng, atoms) for _ in range(200)]
        started = time.perf_counter()
        truth = [brute_force(p) for p in preds]
        brute = (time.perf_counter() - started) * 1000 / len(preds)
        for cap in (64, 4096):
            started = time.perf_counter()
            answers = [check(p, dict(domains), max_clauses=cap) for p in preds]
            solver = (time.perf_counter() - started) * 1000 / len(preds)
            decided = sum(a.status is not Status.UNKNOWN for a in answers)
            wrong = sum(
                (a.status is Status.SAT) != t
                for a, t in zip(answers, truth, strict=True)
                if a.status is not Status.UNKNOWN
            )
            print(
                f"{atoms:>5} {cap:>5} {solver:>10.3f} {brute:>9.1f} {brute / solver:>8.0f}x "
                f"{decided / len(preds):>7.0%} {wrong:>6}"
            )
    print()


# -- part B: generator against random inputs --------------------------------------------------
def random_values(rng: random.Random, domains: dict[str, Domain]) -> dict[str, object]:
    out: dict[str, object] = {}
    for name, d in sorted(domains.items()):
        if d.kind == "int":
            assert d.lo is not None and d.hi is not None
            out[name] = rng.randint(int(d.lo), int(d.hi))
        elif d.kind == "real":
            assert d.lo is not None and d.hi is not None
            out[name] = round(rng.uniform(float(d.lo), float(d.hi)), 3)
        elif d.kind == "bool":
            out[name] = rng.random() < 0.5
        else:
            out[name] = rng.choice(d.values or ("",))
    return out


def part_b(rng: random.Random) -> None:
    runs = 300
    rows: list[tuple[int, int, int, float, float, int]] = []
    for _ in range(runs):
        plan = random_workflow(rng, guard_chance=0.7)
        lowered = lower_guards(plan, SIGNATURES)
        cases = generate(plan, SIGNATURES)
        feasible = [t for t in cases.targets if t not in cases.infeasible]
        if not feasible:
            continue
        reached = {t for c in cases.cases for t in c.covers}

        def covered(
            values: dict[str, object], plan=plan, lowered=lowered, cases=cases
        ) -> set[Target]:
            states = predict(plan, lowered.preds, values)
            return {t for t in cases.targets if states[t.step] == (RUN if t.runs else SKIP)}

        same = set()
        for _ in range(len(cases.cases)):
            same |= covered(random_values(rng, lowered.domains))
        same &= reached
        needed, got = 0, set()
        while got != reached and needed < 2000:
            got |= covered(random_values(rng, lowered.domains)) & reached
            needed += 1
        rows.append(
            (
                len(feasible),
                len(cases.cases),
                needed,
                len(reached) / len(feasible),
                len(same) / len(reached) if reached else 1.0,
                len(cases.missed),
            )
        )
    print(f"B. test generation on {len(rows)} random workflows with at least one feasible target")
    naive = statistics.mean(r[0] for r in rows)
    ours = statistics.mean(r[1] for r in rows)
    rand = statistics.mean(r[2] for r in rows)
    print(f"  cases to cover the targets of the generator: naive {naive:.2f}, ours {ours:.2f},")
    print(
        f"    random inputs {rand:.2f} (stopped at 2,000; {sum(r[2] >= 2000 for r in rows)} hit it)"
    )
    print(f"  feasible targets covered by ours: {statistics.mean(r[3] for r in rows):.1%}")
    equal = statistics.mean(r[4] for r in rows)
    print(f"  targets covered by as many random cases as ours: {equal:.1%}")
    print(f"  workflows with a target the generator could not reach: {sum(r[5] > 0 for r in rows)}")


def main() -> None:
    rng = random.Random(SEED)
    part_a(rng)
    part_b(rng)


if __name__ == "__main__":
    main()
