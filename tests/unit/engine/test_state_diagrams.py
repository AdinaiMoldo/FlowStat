"""The state diagrams of docs/diagrams/ against the code (DOC-9).

``step-states.puml``, ``run-states.puml`` and ``ownership-states.puml`` must have exactly the
states of the code, and every move the code makes in the scenarios below must be an arrow of the
diagram. The converse (every arrow is taken) is checked against a short list of arrows that these
scenarios do not drive, so that a new arrow cannot be drawn without a scenario or a reason.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, ClassVar

import pytest

from flowstat.compiler.plan import Plan
from flowstat.engine.scheduler import Scheduler
from flowstat.model.events import Event, EventKind
from flowstat.model.history import status_of
from flowstat.model.ownership import OwnerState
from flowstat.model.states import RunOutcome, StepState
from flowstat.recovery.saga import compensation_blockers
from tests.unit.engine.helpers import doc, plan_of

ROOT = Path(__file__).resolve().parents[3]
DIAGRAMS = ROOT / "docs" / "diagrams"
SRC = ROOT / "src" / "flowstat"
S = StepState

Edge = tuple[str, str]


def machine(name: str) -> tuple[set[str], set[Edge]]:
    """The states and the arrows (without the start and the end marks) of a state diagram."""
    source = (DIAGRAMS / f"{name}.puml").read_text(encoding="utf8")
    lines = source.strip().splitlines()
    assert lines[0] == f"@startuml {name}" and lines[-1] == "@enduml"
    states = set(re.findall(r"^state (\w+)\s*$", source, re.M))
    arrows = set(re.findall(r"^(\w+) --> (\w+)", source, re.M))
    return states, arrows


# -- the states are those of the code -----------------------------------------------------------
def test_the_step_diagram_has_exactly_the_states_of_the_enumeration() -> None:
    states, arrows = machine("step-states")
    assert states == {s.value for s in StepState}
    assert {a for a, _ in arrows} | {b for _, b in arrows} <= states


def test_the_ownership_diagram_has_exactly_the_states_of_the_enumeration() -> None:
    states, arrows = machine("ownership-states")
    assert states == {s.value for s in OwnerState}
    assert {a for a, _ in arrows} | {b for _, b in arrows} <= states


def literals(path: Path, function: str) -> set[str]:
    """The strings that a function of a module returns."""
    tree = ast.parse(path.read_text(encoding="utf8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function:
            for inner in ast.walk(node):
                if isinstance(inner, ast.Return) and isinstance(inner.value, ast.Constant):
                    found.add(str(inner.value.value))
                if isinstance(inner, ast.Return) and isinstance(inner.value, ast.IfExp):
                    for part in (inner.value.body, inner.value.orelse):
                        if isinstance(part, ast.Constant):
                            found.add(str(part.value))
    return found


def test_the_run_diagram_has_exactly_the_statuses_the_code_returns() -> None:
    states, arrows = machine("run-states")
    in_history = literals(SRC / "model" / "history.py", "_status")
    in_scheduler = literals(SRC / "engine" / "scheduler.py", "status")
    assert in_history == in_scheduler  # the two places that compute a status agree
    assert states == in_history == {"CREATED", "RUNNING", "WAITING", "COMPENSATING", "FINISHED"}
    assert {a for a, _ in arrows} | {b for _, b in arrows} <= states


def test_the_outcomes_the_run_diagram_names_are_those_of_the_enumeration() -> None:
    source = (DIAGRAMS / "run-states.puml").read_text(encoding="utf8")
    named = set(
        re.findall(r"\b(SUCCEEDED|GOAL_NOT_REACHED|FAILED|CANCELLED|COMPENSATED)\b", source)
    )
    named |= {"COMPENSATION_FAILED"} if "COMPENSATION_FAILED" in source else set()
    assert named <= {o.value for o in RunOutcome}
    assert {o.value for o in RunOutcome} == {
        "SUCCEEDED",
        "GOAL_NOT_REACHED",
        "FAILED",
        "CANCELLED",
        "COMPENSATED",
        "COMPENSATION_FAILED",
    }


# -- the moves of the code are arrows -----------------------------------------------------------
class Moves(dict[str, StepState]):
    """The states of a scheduler, recording every change of a state."""

    def __init__(self, states: dict[str, StepState], log: set[Edge]) -> None:
        super().__init__(states)
        self.log = log
        self.on = True

    def __setitem__(self, key: str, value: StepState) -> None:
        before = self.get(key)
        if self.on and before is not None and before is not value:
            self.log.add((before.value, value.value))
        super().__setitem__(key, value)


class Watch:
    """A scheduler whose step moves and run statuses are recorded."""

    steps: ClassVar[set[Edge]] = set()
    runs: ClassVar[set[Edge]] = set()

    def __init__(self, scheduler: Scheduler) -> None:
        self.s = scheduler
        self.moves = Moves(scheduler.states, Watch.steps)
        scheduler.states = self.moves
        self.last = scheduler.status

    def __call__(self, name: str, *args: object, **kwargs: object) -> list[object]:
        seen = len(self.s.events)
        out = getattr(self.s, name)(*args, **kwargs)
        for event in self.s.events[seen:]:
            if event.kind is EventKind.STEP_RETRY:  # the step stays where it is: a loop
                state = self.s.states[event.step or ""].value
                Watch.steps.add((state, state))
        now = self.s.status
        if now != self.last:
            Watch.runs.add((self.last, now))
            self.last = now
        return list(out)


def new(steps: str, goals: str, workers: int = 2, saga: bool = False) -> Watch:
    plan: Plan = plan_of(doc(steps, goals))
    planner = compensation_blockers if saga else None
    return Watch(Scheduler(plan, {}, workers, compensation_planner=planner))


def crashed(watch: Watch, workers: int = 2) -> Watch:
    """The run as a new process finds it: restored from the history, nothing is idempotent.

    The replay of the history is not a move; what ``_recover`` does to a call that was in flight
    (it becomes UNCERTAIN) is, and only that is recorded.
    """
    s = Scheduler(watch.s.plan, {}, workers, compensation_planner=compensation_blockers)
    again = Watch(s)
    again.moves.on = False
    original = s._recover

    def recover(*args: Any) -> None:
        again.moves.on = True
        original(*args)
        again.moves.on = False

    s._recover = recover  # type: ignore[method-assign,assignment]
    s.restore(watch.s.events, 100.0, lambda action: False)
    again.moves.on = True
    if s.status != watch.s.status:  # the restart itself changes the status
        Watch.runs.add((watch.s.status, s.status))
    again.last = s.status
    return again


@pytest.fixture(scope="module", autouse=True)
def scenarios() -> None:
    Watch.steps, Watch.runs = set(), set()
    run_the_scenarios()


def run_the_scenarios() -> None:
    # a run that skips, retries, succeeds, and whose goal holds
    w = new(
        "  a:\n    action: x.y\n  b:\n    action: x.y\n    needs: [a]\n    when: 1 > 2\n"
        "  c:\n    action: x.y\n    needs: [a]\n    retry: {max: 1}\n",
        "goals: {any_of: [c]}\n",
    )
    w("start", 0.0)
    w("step_done", "a", {}, 1.0)
    w("step_failed", "c", "E-ACT-008", "again", 2.0, transient=True)
    w("step_done", "c", {}, 3.0)

    # a failure that stops the run: the independent steps are cancelled, the join cannot hold
    w = new(
        "  a:\n    action: x.y\n  b:\n    action: x.y\n    needs: [a]\n"
        "  c:\n    action: x.ask\n  d:\n    action: x.y\n    needs: [c]\n",
        "goals: {any_of: [b, d]}\n",
    )
    w("start", 0.0)
    w("step_failed", "a", "E-ACT-001", "boom", 1.0)

    # a condition and arguments that cannot be evaluated (the input was not given)
    for step in ("    when: ${inputs.n} > 2\n", '    with: {k: "${inputs.n}"}\n'):
        plan = plan_of(
            doc("  a:\n    action: x.y\n" + step, "goals: {any_of: [a]}\n", "  n: {type: int}\n")
        )
        w = Watch(Scheduler(plan, {}, 1))
        w("start", 0.0)
    # a failure that lets the run go on: the step after it cannot hold its join
    w = new(
        "  a:\n    action: x.y\n    on_failure: continue\n  b:\n    action: x.y\n    needs: [a]\n"
        "  c:\n    action: x.y\n",
        "goals: {any_of: [c]}\n",
    )
    w("start", 0.0)
    w("step_failed", "a", "E-ACT-001", "boom", 1.0)
    w("step_done", "c", {}, 2.0)

    # a wait that is answered, one that runs out of time, one that is cancelled
    w = new(
        "  a:\n    action: x.ask\n  b:\n    action: x.ask\n    timeout: 5\n"
        "  c:\n    action: x.ask\n  d:\n    action: x.y\n    needs: [c]\n",
        "goals: {any_of: [a]}\n",
        workers=3,
    )
    w("start", 0.0)
    for step in ("a", "b", "c"):
        w("step_waiting", step, 1.0)
    w("signal", "a", {}, 2.0)
    w("expire", 10.0)
    w("cancel", 11.0)

    # a cancel before anything ran, and one while a step is READY (one worker)
    w = new("  a:\n    action: x.y\n  b:\n    action: x.y\n", "goals: {any_of: [a]}\n", workers=1)
    w("cancel", 0.0)
    w = new("  a:\n    action: x.y\n  b:\n    action: x.y\n", "goals: {any_of: [a]}\n", workers=1)
    w("start", 0.0)
    w("cancel", 1.0)

    # a failure under on_failure: compensate: undo in reverse order, one undo fails, one is retried
    saga = (
        "  a:\n    action: x.y\n    compensate: x.undo_a\n"
        "  b:\n    action: x.y\n    needs: [a]\n    compensate: x.undo_b\n    retry: {max: 1}\n"
        "  c:\n    action: x.y\n    needs: [b]\n    on_failure: compensate\n"
    )
    w = new(saga, "goals: {any_of: [c]}\n", workers=1, saga=True)
    w("start", 0.0)
    w("step_done", "a", {}, 1.0)
    w("step_done", "b", {}, 2.0)
    w("step_failed", "c", "E-ACT-001", "boom", 3.0)
    w("step_failed", "b", "E-ACT-008", "again", 4.0, transient=True)
    w("step_done", "b", {}, 5.0)
    w("step_failed", "a", "E-ACT-001", "cannot undo", 6.0)

    # a crash in the call of a step that is not idempotent, decided both ways
    for decision in ("done", "retry"):
        w = new("  a:\n    action: x.send\n", "goals: {any_of: [a]}\n", workers=1, saga=True)
        w("start", 0.0)
        again = crashed(w, 1)
        again("resolve", "a", decision, 101.0, {})
    # a crash in the call of a step that is not idempotent, and then the run is cancelled
    w = new("  a:\n    action: x.send\n", "goals: {any_of: [a]}\n", workers=1, saga=True)
    w("start", 0.0)
    again = crashed(w, 1)
    again("cancel", 101.0)
    # a crash in the call of an undo
    w = new(saga, "goals: {any_of: [c]}\n", workers=1, saga=True)
    w("start", 0.0)
    w("step_done", "a", {}, 1.0)
    w("step_done", "b", {}, 2.0)
    w("step_failed", "c", "E-ACT-001", "boom", 3.0)
    for decision in ("done", "retry"):
        again = crashed(w, 1)
        again("resolve", "b", decision, 101.0, {})


def test_every_move_of_a_step_in_the_scenarios_is_an_arrow_of_the_diagram() -> None:
    _, arrows = machine("step-states")
    assert Watch.steps - arrows == set()


def test_every_change_of_the_status_of_a_run_in_the_scenarios_is_an_arrow_of_the_diagram() -> None:
    _, arrows = machine("run-states")
    assert Watch.runs - arrows == set()


def test_the_history_of_every_prefix_of_a_run_has_a_status_that_is_drawn() -> None:
    states, _ = machine("run-states")
    w = new("  a:\n    action: x.y\n", "goals: {any_of: [a]}\n")
    w("start", 0.0)
    w("step_done", "a", {}, 1.0)
    events: list[Event] = w.s.events
    seen = [status_of(events[:k])[0] for k in range(len(events) + 1)]
    assert set(seen) <= states and seen[-1] == "FINISHED"


def test_every_arrow_of_the_step_diagram_is_driven_by_a_scenario() -> None:
    """A new arrow needs a scenario: nothing is drawn that no run of the code has made."""
    _, arrows = machine("step-states")
    assert arrows - Watch.steps == set()


def test_every_arrow_of_the_run_diagram_is_driven_by_a_scenario() -> None:
    _, arrows = machine("run-states")
    assert arrows - Watch.runs == set(), sorted(arrows - Watch.runs)
