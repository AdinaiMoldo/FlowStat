"""FIFO, the plain longest path and the feasible critical path on workflows with guarded branches.

Run: python benchmarks/bench_critical.py

The workflows are random DAGs like those of bench_priority.py (each step needs 0 to 3 of the 12
steps before it; durations are 1 plus an exponential with mean 3, a tenth are six times longer),
with two additions that make the question worth asking: some of the steps have a guard on one
integer input (``amount > t`` or ``amount <= t``, random t), and 40% of the steps with
dependencies use the join rule ``all_done`` (so a chain can pass over a step that was skipped).
Two workloads: A has guards on 40% of the steps and a tenth of the steps six times longer; B has
guards on 70% and a fifth of the steps ten times longer, which puts more of the long work behind
conditions.

Each workflow is run on the simulated clock for ten random values of the input, with four priority
rules:

* fifo: in the order of becoming ready;
* plain: the longest path of M2, which counts every step;
* feasible, all inputs: the feasible critical path with the input unknown (the Proposal's reading);
* feasible, this run: the same with the value of the input put into the guards (what the service
  of M5 does).

The priorities know the true durations. The makespan is divided by the longest chain that really
runs (the makespan with unlimited workers) or by the total work of the steps that run divided
among the workers, whichever is larger; no schedule is below 1.000. The conclusion is kept in
docs/research/priority-rules.md.
"""

from __future__ import annotations

import random
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flowstat.analysis.critical import Critical, priorities
from flowstat.compiler.catalogue import Catalogue
from flowstat.compiler.compile import compile_workflow
from flowstat.compiler.plan import Plan
from flowstat.engine.simulation import simulate

SEED = 20261105
DAGS = 100
VALUES = 10  # values of the input per workflow
WORKLOADS = {"A": (0.4, 0.1, 6), "B": (0.7, 0.2, 10)}  # guarded, long, how much longer


def random_workflow(
    rng: random.Random, steps: int, guarded: float, long: float, factor: int
) -> tuple[Plan, dict[str, float]]:
    lines = [
        "workflow: bench",
        "version: 1",
        "inputs:",
        "  amount: {type: int, min: 0, max: 99}",
        "steps:",
    ]
    durations: dict[str, float] = {}
    for i in range(steps):
        lines += [f"  s{i}:", "    action: x.y"]
        window = list(range(max(0, i - 12), i))
        picked = sorted(rng.sample(window, min(len(window), rng.choice([0, 1, 1, 2, 3]))))
        if picked:
            lines.append("    needs: [" + ", ".join(f"s{j}" for j in picked) + "]")
            if rng.random() < 0.4:
                lines.append("    join: all_done")
        if rng.random() < guarded:
            op = rng.choice([">", "<="])
            lines.append(f'    when: "${{inputs.amount}} {op} {rng.randint(10, 90)}"')
        durations[f"s{i}"] = (1.0 + rng.expovariate(1 / 3)) * (factor if rng.random() < long else 1)
    lines.append(f"goals: {{all_of: [{', '.join(f's{i}' for i in range(steps))}]}}")
    result = compile_workflow("\n".join(lines) + "\n", Catalogue({}))
    assert result.plan is not None, result.diagnostics[:2]
    return result.plan, durations


@dataclass
class Case:
    plan: Plan
    durations: dict[str, float]
    everything: Critical  # priorities with the input unknown
    amount: int
    this_run: Critical
    bound_chain: float
    work: float


def makespan(
    c: Case, workers: int, policy: str = "longest_path", priority: Critical | None = None
) -> float:
    return simulate(
        c.plan,
        {"amount": c.amount},
        workers,
        durations=c.durations,
        policy=policy,
        priorities=None if priority is None else priority.priority,
    ).makespan


def build(rng: random.Random, steps: int, shape: tuple[float, float, int]) -> list[Case]:
    cases: list[Case] = []
    spent = {"all": [], "run": []}  # type: dict[str, list[float]]
    for _ in range(DAGS):
        plan, durations = random_workflow(rng, steps, *shape)
        started = time.perf_counter()
        everything = priorities(plan, {}, durations)
        spent["all"].append(time.perf_counter() - started)
        for _ in range(VALUES):
            amount = rng.randint(0, 99)
            inputs = {"amount": amount}
            started = time.perf_counter()
            this_run = priorities(plan, {}, durations, inputs)
            spent["run"].append(time.perf_counter() - started)
            unlimited = simulate(plan, inputs, 10_000, durations=durations)
            work = sum(durations[s] for s in unlimited.finished)
            cases.append(
                Case(plan, durations, everything, amount, this_run, unlimited.makespan, work)
            )
    ms = {k: (1000 * statistics.mean(v), 1000 * max(v)) for k, v in spent.items()}
    cut = sum(c.everything.cut != frozenset() for c in cases[::VALUES])
    print(
        f"  priorities, {steps} steps: all inputs mean {ms['all'][0]:.1f} ms "
        f"(max {ms['all'][1]:.0f}), this run mean {ms['run'][0]:.1f} ms "
        f"(max {ms['run'][1]:.0f}); cut in {cut} of {DAGS}"
    )
    return cases


def main() -> None:
    for name, shape in WORKLOADS.items():
        print(
            f"Workload {name}: {shape[0]:.0%} guarded, "
            f"{shape[1]:.0%} of the steps {shape[2]}x longer"
        )
        header = ["steps", "workers", "FIFO", "plain", "feasible, all inputs", "feasible, this run"]
        print("| " + " | ".join(header) + " | this run vs plain: better / equal / worse | saved |")
        print("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for steps in (50, 200):
            cases = build(random.Random(SEED + steps), steps, shape)
            for workers in (2, 4, 8):
                ratio: dict[str, list[float]] = {k: [] for k in ("fifo", "plain", "all", "run")}
                saved: list[float] = []
                better = equal = worse = 0
                for c in cases:
                    bound = max(c.bound_chain, c.work / workers)
                    plain = makespan(c, workers)
                    this = makespan(c, workers, priority=c.this_run)
                    ratio["fifo"].append(makespan(c, workers, "fifo") / bound)
                    ratio["plain"].append(plain / bound)
                    ratio["all"].append(makespan(c, workers, priority=c.everything) / bound)
                    ratio["run"].append(this / bound)
                    saved.append(1 - this / plain)
                    better += this < plain - 1e-9
                    worse += this > plain + 1e-9
                    equal += abs(this - plain) <= 1e-9
                print(
                    f"| {steps} | {workers} | {statistics.mean(ratio['fifo']):.3f} | "
                    f"{statistics.mean(ratio['plain']):.3f} | "
                    f"{statistics.mean(ratio['all']):.3f} | "
                    f"{statistics.mean(ratio['run']):.3f} | {better} / {equal} / {worse} | "
                    f"{100 * statistics.mean(saved):.2f}% |"
                )
        print()


if __name__ == "__main__":
    main()
