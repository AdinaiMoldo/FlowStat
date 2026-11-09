"""The cost of the check of version migration (C5, level 1) and of the handover (level 2).

Run: python benchmarks/bench_migration.py

* Conformance: the 9-region example is run on the simulated clock; the check is made for the
  history cut at every event (the moment a person might ask), against the same version and
  against a version that adds a step and changes a later one. The figure is the time of one
  check; the plans are compiled once, as the service does for each run in a listing.

The conclusion is kept in docs/research/m8-measurement.md.
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.unit.engine.helpers import EXAMPLE, catalogue, plan_of

from flowstat.engine.simulation import simulate
from flowstat.migration.conformance import check
from flowstat.recovery.saga import compensation_blockers

INPUTS = {"quarter": "2025Q1"}
GOALS = "goals: {any_of: [publish, return_to_regions]}"
NOTIFY = "  notify:\n    action: email.send\n    needs: [publish]\n"
CHANGED = EXAMPLE.replace("version: 1", "version: 2", 1).replace(GOALS, NOTIFY + GOALS)


def row(name: str, times: list[float]) -> str:
    ordered = sorted(times)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    median = statistics.median(times)
    return f"| {name} | {len(times)} | {median:.3f} | {p95:.3f} | {ordered[-1]:.3f} |"


def conformance() -> list[str]:
    old, same, changed = plan_of(EXAMPLE), plan_of(EXAMPLE), plan_of(CHANGED)
    events = simulate(old, INPUTS, 3, compensation_planner=compensation_blockers).scheduler.events
    cat = catalogue()
    lines = [
        "| Check | Histories | median ms | p95 ms | max ms |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, new in (("same version", same), ("adds a step", changed)):
        times, yes = [], 0
        for cut in range(1, len(events) + 1):
            start = time.perf_counter()
            verdict = check(old, new, events[:cut], INPUTS, {}, cat)
            times.append((time.perf_counter() - start) * 1000)
            yes += verdict.migratable
        lines.append(row(f"{name} ({len(events)} events, {yes} migratable)", times))
    return lines


def main() -> None:
    print("\n".join(conformance()))


if __name__ == "__main__":
    main()
