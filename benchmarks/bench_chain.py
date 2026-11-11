"""Availability and priorities on a long chain of guarded steps (C2, US-43).

Run: python benchmarks/bench_chain.py

Step i needs step i-1 and runs only if its output says so, so the run condition of step n holds
n guards, nested n deep. The table compares the analysis without a bound on the size of a run
condition (``RUN_CAP`` very large, the behaviour before US-43) with the one with the bound.
The conclusion is kept in docs/research/deep-chains.md.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.unit.analysis.helpers import SIGNATURES, compile_plan

from flowstat.analysis import availability, runs
from flowstat.analysis.critical import priorities

SIZES = (100, 250, 500, 1000, 2000)
UNBOUNDED = 10**9


def chain(n: int) -> str:
    out = ["  s0:\n    action: x.y\n"]
    for i in range(1, n):
        out.append(
            f"  s{i}:\n    action: x.y\n    needs: [s{i - 1}]\n"
            f'    when: "${{s{i - 1}.ok}} == true"\n'
        )
    return "".join(out)


def measure(n: int, cap: int) -> str:
    runs.RUN_CAP = cap
    plan = compile_plan(chain(n), f"s{n - 1}", inputs="")
    started = time.perf_counter()
    try:
        availability.analyse(plan, SIGNATURES)
        priorities(plan, SIGNATURES)
    except RecursionError:
        return "RecursionError"
    return f"{time.perf_counter() - started:.2f} s"


def main() -> None:
    cap = runs.RUN_CAP
    print(f"| steps | lines | without the bound | with the bound (RUN_CAP = {cap}) |")
    print("| --- | --- | --- | --- |")
    for n in SIZES:
        before = measure(n, UNBOUNDED)
        after = measure(n, cap)
        print(f"| {n} | {5 * n} | {before} | {after} |")


if __name__ == "__main__":
    main()
