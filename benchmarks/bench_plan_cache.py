"""The cost of compiling the plan of a published version again and again, and of not doing it
(US-41, M9).

Run: python benchmarks/bench_plan_cache.py

The same requests are measured twice in one process with the code that serves, through the test
client of FastAPI (so the figures are the work of the app and contain no network): with the cache
turned off (a cache of size 0 compiles every time, as the code did before) and with the default
cache. A request that does not ask for a plan (`GET /runs`, which reads the status from the events)
is measured as well, to show that it is not changed. The conclusion is kept in
docs/research/plan-cache-measurement.md.
"""

from __future__ import annotations

import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.unit.actions.helpers import write_all_forms
from tests.unit.api.helpers import EXAMPLE, NAME, Env, make_env

from flowstat.compiler.cache import PlanCache

QUARTER = {"inputs": {"quarter": "2025Q1"}}
REPEAT = 100
RUNS = 50


def timed(call: Callable[[], object], repeat: int = REPEAT) -> list[float]:
    times = []
    for _ in range(repeat):
        start = time.perf_counter()
        call()
        times.append((time.perf_counter() - start) * 1000)
    return times


def prepared(root: Path) -> Env:
    root.mkdir(parents=True, exist_ok=True)
    env = make_env(root)
    env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish")
    write_all_forms(env.config.data_dir, "2025Q1")
    for _ in range(RUNS):
        assert (
            env.call("operator", "POST", f"/workflows/{NAME}/runs", json=QUARTER).status_code == 202
        )
    return env


def measure(env: Env, size: int) -> dict[str, float]:
    registry = env.client.app.state.registry  # type: ignore[attr-defined]
    registry.plans = PlanCache(size)
    calls: dict[str, Callable[[], object]] = {
        "GET /runs/{id} (report)": lambda: env.call("operator", "GET", "/runs/r-0001"),
        "GET /workflows/{name}/versions/1/graph": lambda: env.call(
            "operator", "GET", f"/workflows/{NAME}/versions/1/graph"
        ),
        "GET /runs (50 runs)": lambda: env.call("operator", "GET", "/runs"),
        "POST start a run (to the approval)": lambda: env.call(
            "operator", "POST", f"/workflows/{NAME}/runs", json=QUARTER
        ),
    }
    found = {}
    for name, call in calls.items():
        repeat = 30 if name.startswith("POST") else REPEAT
        call()  # one call first: the first one fills the cache and is not what is measured
        found[name] = statistics.median(timed(call, repeat))
    return found


def main() -> None:
    with tempfile.TemporaryDirectory() as root:
        # two databases that are alike (50 runs each), so that the second measurement does not
        # see the runs the first one started
        off = measure(prepared(Path(root) / "off"), 0)
        env = prepared(Path(root) / "on")
        on = measure(env, 64)
        kept = env.client.app.state.registry.plans  # type: ignore[attr-defined]
    print(
        f"{RUNS} runs stored, the example workflow of 25 steps, "
        f"median of {REPEAT} calls (30 for POST)"
    )
    print()
    print("| request | no cache: median ms | cache: median ms | faster |")
    print("| --- | --- | --- | --- |")
    for name in off:
        print(f"| {name} | {off[name]:.1f} | {on[name]:.1f} | {off[name] / on[name]:.1f}x |")
    print()
    print(f"the cache after the run: {kept.hits} hits, {kept.misses} misses, {len(kept)} plan kept")


if __name__ == "__main__":
    main()
