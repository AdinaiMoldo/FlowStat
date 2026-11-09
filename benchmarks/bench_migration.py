"""The cost of the check of version migration (C5, level 1) and of the handover (level 2).

Run: python benchmarks/bench_migration.py

* Handover: a run that waits for an approval is moved between the core and two services (three
  in-memory databases, calls in-process). Every call of the protocol is a crash point; each is
  crashed before and after, and once more during the recovery. After each recovery the stores are
  asked who owns the run and whether the history is whole. The baseline is "stop and restart": the
  old run is dropped and a new run of the new version is started with the same inputs.
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


def handover() -> list[str]:
    import contextlib

    from tests.crash import test_handover as t

    def sound(w: t.World, source: str, target: str, original: list[t.Event]) -> bool:
        try:
            t.assert_sound(w, source, target, original)
        except AssertionError:
            return False
        return True

    lines = [
        "| Move | one crash | sound | two crashes | sound | median ms |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for source, target in t.PAIRS:
        total = t.count_calls(source, target)
        one = one_ok = two = two_ok = 0
        for at, when in t.points(total):
            w = t.world()
            original = t.start(w, source)
            plan = t.plan_for(source, target, original)
            with contextlib.suppress(t.Crash):
                t.move(plan, *t.faulty(w, t.Fault(at, when)))
            t.recover_all(*t.clean(w))
            one += 1
            one_ok += sound(w, source, target, original)
            probe = t.world()
            t.start(probe, source)
            with contextlib.suppress(t.Crash):
                t.move(plan, *t.faulty(probe, t.Fault(at, when)))
            counter = t.Fault()
            t.recover_all(*t.faulty(probe, counter))
            for again, how in t.points(counter.calls):
                w = t.world()
                original = t.start(w, source)
                with contextlib.suppress(t.Crash):
                    t.move(plan, *t.faulty(w, t.Fault(at, when)))
                with contextlib.suppress(t.Crash):
                    t.recover_all(*t.faulty(w, t.Fault(again, how)))
                t.recover_all(*t.clean(w))
                two += 1
                two_ok += sound(w, source, target, original)
        times = []
        for _ in range(30):
            w = t.world()
            plan = t.plan_for(source, target, t.start(w, source))
            began = time.perf_counter()
            t.move(plan, *t.clean(w))
            times.append((time.perf_counter() - began) * 1000)
        lines.append(
            f"| {source} -> {target} | {one} | {one_ok}/{one} | {two} | {two_ok}/{two} | "
            f"{statistics.median(times):.3f} |"
        )
    original = t.waiting_history()
    fresh = t.simulate(t.new_plan(), t.INPUTS, 1, t.behavior).scheduler.events
    redone = sum(1 for e in fresh if e.kind is t.K.STEP_STARTED and e.step in {"a", "b"})
    before = sum(1 for e in original if e.kind is t.K.STEP_FINISHED)
    lines += [
        "",
        "| Method | runs kept | steps finished before | steps done again |",
        "| --- | --- | --- | --- |",
        f"| handover | 1/1 | {before} | 0 |",
        f"| stop and restart | 0/1 | {before} | {redone} |",
    ]
    return lines


def main() -> None:
    print("\n".join(conformance()))
    print()
    print("\n".join(handover()))


if __name__ == "__main__":
    main()
