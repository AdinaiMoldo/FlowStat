"""FIFO against the plain longest path on generated workflows.

Run: python benchmarks/bench_priority.py

For random DAGs with known step durations the benchmark runs the scheduler on a simulated clock
with both queue policies and compares the makespan (total virtual time of the run). The result is
the baseline for the feasible critical path of M5; the conclusion is kept in
docs/research/priority-rules.md.
"""

from __future__ import annotations

import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flowstat.compiler.catalogue import Catalogue
from flowstat.compiler.compile import compile_workflow
from flowstat.compiler.plan import Plan
from flowstat.engine.simulation import simulate

SEED = 20261105
DAGS = 100


def random_workflow(rng: random.Random, steps: int) -> tuple[Plan, dict[str, float]]:
    """A random DAG: each step needs 0 to 3 of the 12 steps before it, so the graph has depth."""
    lines = ["workflow: bench", "version: 1", "steps:"]
    durations: dict[str, float] = {}
    for i in range(steps):
        lines += [f"  s{i}:", "    action: x.y"]
        window = list(range(max(0, i - 12), i))
        picked = sorted(rng.sample(window, min(len(window), rng.choice([0, 1, 1, 2, 3]))))
        if picked:
            lines.append("    needs: [" + ", ".join(f"s{j}" for j in picked) + "]")
        base = 1.0 + rng.expovariate(1 / 3)  # most steps are short ...
        durations[f"s{i}"] = base * (6 if rng.random() < 0.1 else 1)  # ... a tenth are long
    lines.append(f"goals: {{all_of: [{', '.join(f's{i}' for i in range(steps))}]}}")
    result = compile_workflow("\n".join(lines) + "\n", Catalogue({}))
    assert result.plan is not None, result.diagnostics[:2]
    return result.plan, durations


def lower_bound(plan: Plan, durations: dict[str, float], workers: int) -> float:
    """No schedule beats the longest chain or the total work divided among the workers."""
    longest: dict[str, float] = {}
    for step in plan.steps:  # plan.steps is topological
        longest[step.id] = durations[step.id] + max((longest[p] for p in step.needs), default=0.0)
    return max(max(longest.values()), sum(durations.values()) / workers)


def main() -> None:
    header = ["steps", "workers", "FIFO / bound", "longest path / bound", "makespan saved"]
    header.append("better / equal / worse")
    print("| " + " | ".join(header) + " |")
    print("| --- | --- | --- | --- | --- | --- |")
    for steps in (50, 200):
        rng = random.Random(SEED + steps)
        cases = [random_workflow(rng, steps) for _ in range(DAGS)]
        for workers in (2, 4, 8):
            fifo_ratio, best_ratio, saved = [], [], []
            better = equal = worse = 0
            for plan, durations in cases:
                bound = lower_bound(plan, durations, workers)
                fifo = simulate(plan, {}, workers, durations=durations, policy="fifo").makespan
                best = simulate(plan, {}, workers, durations=durations).makespan
                fifo_ratio.append(fifo / bound)
                best_ratio.append(best / bound)
                saved.append(1 - best / fifo)
                if best < fifo - 1e-9:
                    better += 1
                elif best > fifo + 1e-9:
                    worse += 1
                else:
                    equal += 1
            print(
                f"| {steps} | {workers} | {statistics.mean(fifo_ratio):.3f} | "
                f"{statistics.mean(best_ratio):.3f} | {100 * statistics.mean(saved):.1f}% | "
                f"{better} / {equal} / {worse} |"
            )


if __name__ == "__main__":
    main()
