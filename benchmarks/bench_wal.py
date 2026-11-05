"""What it costs to store the history before the commands run (C4, M3).

Run: python benchmarks/bench_wal.py

The nine-region example runs on the thread-pool driver (no waiting, actions return at once) with
a database file on disk and three ways of keeping the history:

* before the commands, one commit per call into the scheduler, ``synchronous = FULL`` (ours);
* the same with ``synchronous = OFF`` (what the guarantee costs on top of the transactions);
* after the run, one commit of all events (the way of M2, no write-ahead).

Only the time spent in the store is measured, so the numbers do not depend on how fast the actions
are. The conclusion is kept in docs/research/recovery-measurement.md.
"""

from __future__ import annotations

import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_recovery import example_workloads

from flowstat.engine.runner import Executor, Ok, Outcome, Pause, run_threaded
from flowstat.engine.scheduler import Scheduler, Start
from flowstat.engine.simulation import Done, Wait
from flowstat.recovery.wal import WriteAheadLog
from flowstat.repo.db import open_database
from flowstat.repo.runs import RunStore

REPEATS = 15


def measure(mode: str) -> tuple[int, int, float]:
    """Returns (events, commits, seconds spent in the store) for one run."""
    load = example_workloads()[0]

    def execute(command: Start) -> Outcome:
        result = load.behavior(command)
        if isinstance(result, Wait):
            return Pause()
        assert isinstance(result, Done)
        return Ok(dict(result.output or {}))

    executor: Executor = execute
    with tempfile.TemporaryDirectory() as folder:
        db = open_database(str(Path(folder) / "flowstat.db"))
        if mode == "synchronous OFF":
            db.execute("PRAGMA synchronous = OFF")
        store = RunStore(db)
        store.create("r1", "w", 1, {}, "T")
        scheduler = Scheduler(load.plan, load.inputs, load.workers)
        log = WriteAheadLog(store, "r1")
        spent, commits = 0.0, 0

        def journal() -> None:
            nonlocal spent, commits
            before = time.perf_counter()
            log.commit(scheduler.events)
            spent += time.perf_counter() - before
            commits += 1

        ticks = iter(range(100_000))
        answer: Callable[[Start], dict[str, object] | None] = lambda c: {"decision": "approved"}  # noqa: E731
        run_threaded(
            scheduler,
            executor,
            lambda: float(next(ticks)),
            lambda s: None,
            answer,
            journal=journal if mode != "once, after the run" else None,
        )
        if mode == "once, after the run":
            before = time.perf_counter()
            log.commit(scheduler.events)
            spent, commits = time.perf_counter() - before, 1
        db.close()
        return len(scheduler.events), commits, spent


def main() -> None:
    print(
        "| history is stored | commits | events | time in the store (median of 15) | per commit |"
    )
    print("| --- | --- | --- | --- | --- |")
    for mode in ("before the commands, synchronous FULL", "synchronous OFF", "once, after the run"):
        runs = [measure(mode) for _ in range(REPEATS)]
        events, commits, _ = runs[0]
        total = statistics.median(r[2] for r in runs)
        print(
            f"| {mode} | {commits} | {events} | {1000 * total:.1f} ms | "
            f"{1000 * total / commits:.2f} ms |"
        )


if __name__ == "__main__":
    main()
