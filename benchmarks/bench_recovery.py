"""Recovery and compensation against their baselines (C4, M3).

Run: python benchmarks/bench_recovery.py

Part A cuts the history of a run after every event (a crash), restores it and finishes it under
three strategies, and counts the calls that are made twice:

* restart from scratch: the history is dropped, the run begins again;
* plain replay: the history is kept and every step that was RUNNING is run again, as if every
  action were idempotent (this is our restore without the UNCERTAIN rule);
* ours: the history is kept; a step that was running is repeated only when its action is
  idempotent, otherwise it becomes UNCERTAIN and a person decides.

Part B runs random failing sagas with three orders of compensation: reverse plan order one at a
time, only the direct dependencies among the steps to undo, and ours (through all dependencies,
in parallel).

The workflows are generated from a fixed seed; durations are virtual time (simulated clock).
The conclusion is kept in docs/research/recovery-measurement.md.
"""

from __future__ import annotations

import random
import statistics
import sys
from collections.abc import Callable, Collection, Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flowstat.compiler.catalogue import Catalogue, load_catalogue
from flowstat.compiler.compile import compile_workflow
from flowstat.compiler.plan import Plan
from flowstat.dsl.reader import read_yaml
from flowstat.engine.scheduler import Start
from flowstat.engine.simulation import Done, Fail, Result, Simulation, Wait, simulate
from flowstat.model.events import Event, EventKind
from flowstat.model.states import StepState
from flowstat.recovery.saga import compensation_blockers

ROOT = Path(__file__).resolve().parents[1]
SEED = 20261105
K = EventKind
NON_IDEMPOTENT = "x.send"


def idempotent(action: str) -> bool:
    return action != NON_IDEMPOTENT and action != "email.send"


# -- workloads ---------------------------------------------------------------------------------
class Workload:
    def __init__(
        self,
        name: str,
        plan: Plan,
        inputs: Mapping[str, object],
        behavior: Callable[[Start], Result],
        workers: int,
        answers: Mapping[str, tuple[float, Mapping[str, object]]] | None = None,
    ) -> None:
        self.name, self.plan, self.inputs = name, plan, inputs
        self.behavior, self.workers, self.answers = behavior, workers, answers

    def run(self, resume: list[Event] | None = None, plain: bool = False) -> Simulation:
        return simulate(
            self.plan,
            self.inputs,
            self.workers,
            self.behavior,
            answers=self.answers,
            compensation_planner=compensation_blockers,
            resume=resume,
            idempotent=(lambda action: True) if plain else idempotent,
        )


def example_workloads() -> list[Workload]:
    tree, _ = read_yaml((ROOT / "examples" / "catalogue.yaml").read_text(encoding="utf8"))
    assert tree is not None
    catalogue, _ = load_catalogue(tree)
    text = (ROOT / "examples" / "quarterly_consolidation.yaml").read_text(encoding="utf8")
    result = compile_workflow(text, catalogue)
    assert result.plan is not None
    rows = [{"code": "I01", "name": "Population", "value": 100.0}]

    def act(command: Start) -> Result:
        step = command.step
        if step.startswith("load") or step.startswith("check"):
            return Done(1.0, {"rows": rows, "count": 1, "checked": 1})
        if step == "consolidate":
            return Done(2.0, {"rows": rows, "regions": 9})
        if step == "approve":
            return Wait(0.5)
        if step == "publish":
            return Done(1.0, {"written": 1, "batch": "r"})
        return Done(1.0, {"queued": 1})

    inputs = {"quarter": "2025Q1"}
    return [
        Workload("nine regions, approved", result.plan, inputs, act, 4,
                 {"approve": (1.0, {"decision": "approved"})}),
        Workload("nine regions, rejected (mail)", result.plan, inputs, act, 4,
                 {"approve": (1.0, {"decision": "rejected"})}),
    ]  # fmt: skip


def random_plan(rng: random.Random, steps: int, with_undo: bool = False) -> tuple[Plan, int]:
    """A random DAG; one step in five sends a mail (not idempotent). Returns the plan and the
    index of the step that fails when ``with_undo``."""
    failing = rng.randrange(steps // 2, steps)
    lines = ["workflow: bench", "version: 1", "steps:"]
    for i in range(steps):
        action = NON_IDEMPOTENT if not with_undo and rng.random() < 0.2 else "x.y"
        lines += [f"  s{i}:", f"    action: {action}"]
        window = list(range(max(0, i - 8), i))
        picked = sorted(rng.sample(window, min(len(window), rng.choice([0, 1, 1, 2]))))
        if picked:
            lines.append("    needs: [" + ", ".join(f"s{j}" for j in picked) + "]")
        if with_undo and rng.random() < 0.6:
            lines.append("    compensate: x.undo")
        if with_undo and i == failing:
            lines.append("    on_failure: compensate")
    lines.append(f"goals: {{any_of: [s{steps - 1}]}}")
    result = compile_workflow("\n".join(lines) + "\n", Catalogue({}))
    assert result.plan is not None, result.diagnostics[:2]
    return result.plan, failing


def random_workloads(count: int, steps: int) -> list[Workload]:
    rng = random.Random(SEED)
    found = []
    for n in range(count):
        plan, _ = random_plan(rng, steps)
        times = {s.id: 1.0 + rng.expovariate(1 / 2) for s in plan.steps}
        found.append(Workload(f"random {n}", plan, {}, lambda c, t=times: Done(t[c.step], {}), 3))
    return found


# -- part A: recovery --------------------------------------------------------------------------
class Tally:
    def __init__(self) -> None:
        self.cuts = 0
        self.repeated = 0  # calls of steps that had been started before the crash
        self.wasted = 0  # ... of which the step had already finished
        self.duplicates = 0  # ... of which the action is not idempotent (the effect may exist)
        self.asked = 0  # steps left to a person
        self.remaining: list[float] = []  # time from the crash to the end, as a share of the run

    def add(self, prefix: list[Event], calls: list[Start], plan: Plan, asked: int) -> None:
        started = {e.step for e in prefix if e.kind is K.STEP_STARTED}
        done = {e.step for e in prefix if e.kind is K.STEP_FINISHED}
        action = {s.id: s.action for s in plan.steps}
        self.cuts += 1
        for call in calls:
            if call.step in started and not call.compensation:
                self.repeated += 1
                self.wasted += call.step in done
                self.duplicates += not idempotent(action[call.step])
        self.asked += asked


def recovery_row(load: Workload) -> list[str]:
    reference = load.run()
    events = reference.scheduler.events
    scratch, plain, ours = Tally(), Tally(), Tally()
    for cut in range(1, len(events)):
        prefix = events[:cut]
        at = prefix[-1].at
        fresh = load.run()
        scratch.add(prefix, fresh.calls, load.plan, 0)
        scratch.remaining.append(fresh.makespan / reference.makespan)
        again = load.run(prefix, plain=True)
        plain.add(prefix, again.calls, load.plan, 0)
        plain.remaining.append((again.makespan - at) / reference.makespan)
        mine = load.run(prefix)
        asked = sum(1 for s in mine.scheduler.states.values() if s is StepState.UNCERTAIN)
        ours.add(prefix, mine.calls, load.plan, asked)
        ours.remaining.append((mine.makespan - at) / reference.makespan)
    cells = [load.name, str(len(events) - 1)]
    for t in (scratch, plain, ours):
        cells.append(f"{t.repeated} / {t.wasted} / {t.duplicates}")
    cells.append(str(ours.asked))
    for t in (scratch, plain, ours):
        cells.append(f"{100 * statistics.mean(t.remaining):.0f}%")
    return cells


def part_a() -> None:
    print("### A. A crash after every event\n")
    print("Cells `repeated / of them finished work / possible duplicate effects`, summed over all")
    print("crash points of the workload.\n")
    head = ["workload", "crash points", "restart from scratch", "plain replay", "ours",
            "left to a person (ours)", "time left: scratch", "plain", "ours"]  # fmt: skip
    print("| " + " | ".join(head) + " |")
    print("|" + " --- |" * len(head))
    loads = example_workloads()
    for load in loads:
        print("| " + " | ".join(recovery_row(load)) + " |")
    pool = random_workloads(40, 25)
    totals = [recovery_row(load) for load in pool]

    def column(i: int, part: int) -> int:
        return sum(int(row[i].split(" / ")[part]) for row in totals)

    cells = ["40 random DAGs of 25 steps (a fifth send mail)", str(sum(int(r[1]) for r in totals))]
    for i in (2, 3, 4):
        cells.append(" / ".join(str(column(i, p)) for p in range(3)))
    cells.append(str(sum(int(r[5]) for r in totals)))
    for i in (6, 7, 8):
        cells.append(f"{statistics.mean(int(r[i][:-1]) for r in totals):.0f}%")
    print("| " + " | ".join(cells) + " |")


# -- part B: compensation ----------------------------------------------------------------------
def descendants(plan: Plan) -> dict[str, set[str]]:
    followers: dict[str, list[str]] = {s.id: [] for s in plan.steps}
    for s in plan.steps:
        for p in s.needs:
            followers[p].append(s.id)
    below: dict[str, set[str]] = {}
    for s in reversed(plan.steps):  # plan.steps is topological
        below[s.id] = set()
        for f in followers[s.id]:
            below[s.id] |= {f} | below[f]
    return below


def direct_only(plan: Plan, to_undo: Collection[str]) -> dict[str, frozenset[str]]:
    """Naive: a step waits only for the steps that directly depend on it."""
    wanted = set(to_undo)
    return {
        s: frozenset(f.id for f in plan.steps if s in f.needs and f.id in wanted) for s in wanted
    }


def reverse_plan_order(plan: Plan, to_undo: Collection[str]) -> dict[str, frozenset[str]]:
    """Naive: undo one at a time, the last step of the plan first."""
    order = [s.id for s in plan.steps if s.id in set(to_undo)]
    return {s: frozenset(order[order.index(s) + 1 :]) for s in order}


def part_b() -> None:
    print("\n### B. The order of a compensation\n")
    rng = random.Random(SEED + 1)
    names = [("reverse plan order, one at a time", reverse_plan_order),
             ("direct dependencies only", direct_only),
             ("ours", compensation_blockers)]  # fmt: skip
    wrong = {n: 0 for n, _ in names}
    spans: dict[str, list[float]] = {n: [] for n, _ in names}
    undone = {n: 0 for n, _ in names}
    cases = 200
    for _ in range(cases):
        plan, failing = random_plan(rng, rng.choice([10, 20, 30]), with_undo=True)
        undo_time = {s.id: 1.0 + rng.random() * 2 for s in plan.steps}
        below = descendants(plan)

        def act(
            command: Start, failing: str = f"s{failing}", times: dict[str, float] = undo_time
        ) -> Result:
            if command.compensation:
                return Done(times[command.step], {})
            return Fail(1.0, "E-ACT-002", "x") if command.step == failing else Done(1.0, {})

        for name, planner in names:
            sim = simulate(plan, {}, 4, act, compensation_planner=planner)
            ran = sim.undone
            undone[name] += len(ran)
            for step, (begin, _) in ran.items():  # a step undone before one it must follow
                if any(d in ran and ran[d][1] > begin + 1e-9 for d in below[step]):
                    wrong[name] += 1
            if ran:
                spans[name].append(
                    max(e for _, e in ran.values()) - min(b for b, _ in ran.values())
                )
    head = ["order", "undo calls", "undone too early", "mean time of the compensation"]
    print("| " + " | ".join(head) + " |")
    print("|" + " --- |" * len(head))
    base = statistics.mean(spans["ours"])
    for name, _ in names:
        mean = statistics.mean(spans[name])
        print(f"| {name} | {undone[name]} | {wrong[name]} | {mean:.1f} ({mean / base:.2f}x ours) |")
    print(
        f"\n{cases} random failing sagas of 10, 20 and 30 steps, 4 workers; "
        "60% of the steps can be undone."
    )


def main() -> None:
    part_a()
    part_b()


if __name__ == "__main__":
    main()
