"""A crash at every step of a handover (NFR-05, DR-01, US-33).

Three databases stand for the core and two services. A run that waits for an approval is moved
between them. The process "dies" before or after every call of the protocol (the calls to either
owner, the journal and the mirror), then restarts and recovers; once more with a second crash in
the middle of the recovery. After every such history: exactly one store owns the run, with the
highest epoch; its history is the original plus one RUN_MIGRATED, with no event lost or doubled;
the core's copy names the owner; and the run goes on to the same end as one that was never moved.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import pytest

from flowstat.compiler.plan import Plan as WorkflowPlan
from flowstat.engine.simulation import Done, Result, Wait, simulate
from flowstat.migration.handover import Journal, Mirror, Owner, Plan, move, recover_all
from flowstat.model.events import Event, EventKind
from flowstat.model.ownership import Ownership
from flowstat.model.states import StepState
from flowstat.repo.db import open_database
from flowstat.repo.runs import RunStore
from flowstat.service.handover import CoreJournal, CoreMirror, LocalOwner
from tests.unit.engine.helpers import doc, plan_of

K = EventKind
RUN = "r-1"
INPUTS = {"n": 5}
OLD = (
    "  a:\n    action: x.y\n"
    "  b:\n    action: x.ask\n    needs: [a]\n"
    "  c:\n    action: x.y\n    needs: [b]\n"
)
NEW = OLD + "  d:\n    action: x.y\n    needs: [c]\n"
ANSWER = {"b": (1.0, {"decision": "approved"})}


def old_plan() -> WorkflowPlan:
    return plan_of(doc(OLD, "goals: {any_of: [c]}\n", "  n: {type: int}\n"))


def new_plan() -> WorkflowPlan:
    return plan_of(doc(NEW, "goals: {any_of: [d]}\n", "  n: {type: int}\n"))


def behavior(start: object) -> Result:
    return Wait() if getattr(start, "step", "") == "b" else Done(1.0, {})


def waiting_history() -> list[Event]:
    events = simulate(old_plan(), INPUTS, 1, behavior).scheduler.events
    cut = next(i for i, e in enumerate(events) if e.kind is K.STEP_WAITING)
    return events[: cut + 1]


class Crash(Exception):
    """The process died."""


@dataclass
class Fault:
    """Dies before or after the ``at``-th call of the protocol (counted from 1; None: never)."""

    at: int | None = None
    when: str = "before"
    calls: int = 0
    log: list[str] = field(default_factory=list)

    def wrap(self, name: str, call: Callable[..., object]) -> Callable[..., object]:
        def run(*args: object) -> object:
            self.calls += 1
            mine = self.calls == self.at
            self.log.append(name)
            if mine and self.when == "before":
                raise Crash(name)
            result = call(*args)
            if mine and self.when == "after":
                raise Crash(name)
            return result

        return run


class Faulty:
    """An owner, a journal or a mirror whose calls count and may die."""

    def __init__(self, name: str, inner: object, fault: Fault) -> None:
        self._name, self._inner, self._fault = name, inner, fault

    def __getattr__(self, attr: str) -> object:
        target = getattr(self._inner, attr)
        if not callable(target):
            return target
        return self._fault.wrap(f"{self._name}.{attr}", target)


class Free:
    def try_acquire(self, run_id: str) -> bool:
        return True

    def release(self, run_id: str) -> None:
        pass


@dataclass
class World:
    """The core and two services, each with its own database."""

    stores: dict[str, RunStore]
    owners: dict[str, LocalOwner]
    journal: CoreJournal
    mirror: CoreMirror


def world() -> World:
    dbs = {name: open_database(":memory:") for name in ("core", "svc1", "svc2")}
    return World(
        {n: RunStore(db) for n, db in dbs.items()},
        {n: LocalOwner(db, Free()) for n, db in dbs.items()},
        CoreJournal(dbs["core"]),
        CoreMirror(dbs["core"]),
    )


def start(w: World, owner: str) -> list[Event]:
    """The run waits at ``owner``; the core has a copy that lags by one event if it is a service."""
    events = waiting_history()
    w.stores[owner].create(RUN, "demo", 1, dict(INPUTS), "t", origin=None)
    w.stores[owner].append(RUN, events)
    if owner != "core":
        w.stores["core"].create(RUN, "demo", 1, dict(INPUTS), "t", origin=owner)
        w.stores["core"].append(RUN, events[:-1])
    return events


def plan_for(source: str, target: str, events: list[Event]) -> Plan:
    return Plan(RUN, source, target, 0, 2, INPUTS, events[-1].seq, 50.0)


def faulty(w: World, fault: Fault) -> tuple[dict[str, Owner], Journal, Mirror]:
    owners: dict[str, Owner] = {n: Faulty(n, o, fault) for n, o in w.owners.items()}  # type: ignore[misc]
    return owners, Faulty("journal", w.journal, fault), Faulty("mirror", w.mirror, fault)  # type: ignore[return-value]


def clean(w: World) -> tuple[dict[str, Owner], Journal, Mirror]:
    return dict(w.owners), w.journal, w.mirror


def owner_of(w: World) -> tuple[str, Ownership]:
    """The one store that drives the run: it has the run, as its own, and holds it OWNED."""
    found = []
    for name, store in w.stores.items():
        try:
            run = store.get(RUN)
        except LookupError:
            continue
        mine = w.owners[name].ownership(RUN)
        if run.origin is None and mine.owned:
            found.append((name, mine))
    assert len(found) == 1, f"owners: {[n for n, _ in found]}"
    return found[0]


def finish(w: World, name: str, version: int) -> dict[str, StepState]:
    """The states the run ends in when it goes on where it is, with the answer to the approval."""
    run = w.stores[name].get(RUN)
    plan = new_plan() if version == 2 else old_plan()
    sim = simulate(plan, run.inputs, 1, behavior, answers=ANSWER, resume=w.stores[name].events(RUN))
    return dict(sim.scheduler.states)


def control(version: int) -> dict[str, StepState]:
    plan = new_plan() if version == 2 else old_plan()
    return dict(simulate(plan, INPUTS, 1, behavior, answers=ANSWER).scheduler.states)


def assert_sound(w: World, source: str, target: str, original: list[Event]) -> None:
    name, mine = owner_of(w)
    events = w.stores[name].events(RUN)
    seqs = [e.seq for e in events]
    assert seqs == list(range(len(seqs))), "a gap or a double in the history"
    if name == source:  # the handover was abandoned: nothing changed
        assert (mine.epoch, w.stores[name].get(RUN).version) == (0, 1)
        assert events == original
        assert finish(w, name, 1) == control(1)
    else:  # completed
        assert name == target and mine.epoch == 1 and mine.peer == source
        assert events[: len(original)] == original
        extra = events[len(original) :]
        assert [e.kind for e in extra] == [K.RUN_MIGRATED] and extra[0].attempt == 1
        assert w.stores[name].get(RUN).version == 2
        assert finish(w, name, 2) == control(2)
        old = w.owners[source].ownership(RUN)
        assert old.epoch == 1 and not old.owned and old.state.value == "MIGRATED_OUT"
    if name != "core":  # the core's copy says who drives the run, and what it is
        copy = w.stores["core"].get(RUN)
        assert (copy.origin, copy.version) == (name, w.stores[name].get(RUN).version)
    elif source != "core":
        assert w.stores["core"].get(RUN).origin is None
    assert w.journal.pending() == [], "a handover was left in the journal"


def count_calls(source: str, target: str) -> int:
    """How many calls a move without a crash makes."""
    w = world()
    plan = plan_for(source, target, start(w, source))
    fault = Fault()
    move(plan, *faulty(w, fault))
    return fault.calls


PAIRS = [("core", "svc1"), ("svc1", "core"), ("svc1", "svc2")]


def points(n: int) -> Iterator[tuple[int, str]]:
    return itertools.product(range(1, n + 1), ("before", "after"))


@pytest.mark.parametrize(("source", "target"), PAIRS)
def test_the_run_has_one_owner_after_a_crash_at_every_call_of_the_move(
    source: str, target: str
) -> None:
    total = count_calls(source, target)
    assert total == 7  # begin, fence, export, adopt, release, mirror, end
    for at, when in points(total):
        w = world()
        original = start(w, source)
        plan = plan_for(source, target, original)
        with pytest.raises(Crash):
            move(plan, *faulty(w, Fault(at, when)))
        recover_all(*clean(w))
        assert_sound(w, source, target, original)


@pytest.mark.parametrize(("source", "target"), PAIRS)
def test_a_second_crash_in_the_middle_of_the_recovery_is_taken_forward_too(
    source: str, target: str
) -> None:
    total = count_calls(source, target)
    for at, when in points(total):
        probe = world()
        events = start(probe, source)
        with pytest.raises(Crash):
            move(plan_for(source, target, events), *faulty(probe, Fault(at, when)))
        counter = Fault()
        recover_all(*faulty(probe, counter))
        for again, how in points(counter.calls):
            w = world()
            original = start(w, source)
            with pytest.raises(Crash):
                move(plan_for(source, target, original), *faulty(w, Fault(at, when)))
            with pytest.raises(Crash):
                recover_all(*faulty(w, Fault(again, how)))
            recover_all(*clean(w))
            assert_sound(w, source, target, original)


def test_a_move_without_a_crash_completes_and_leaves_nothing_in_the_journal() -> None:
    for source, target in PAIRS:
        w = world()
        original = start(w, source)
        done = move(plan_for(source, target, original), *clean(w))
        assert done.epoch == 1 and done.owned
        assert_sound(w, source, target, original)


def test_a_run_moves_to_the_same_store_in_one_step_and_a_crash_cannot_split_it() -> None:
    w = world()
    original = start(w, "core")
    plan = plan_for("core", "core", original)
    with pytest.raises(Crash):
        move(plan, *faulty(w, Fault(1, "before")))
    assert w.owners["core"].ownership(RUN).epoch == 0  # nothing happened
    with pytest.raises(Crash):
        move(plan, *faulty(w, Fault(1, "after")))  # it happened, the caller did not hear
    done = move(plan, *clean(w))  # the same call again is a repeat
    assert (
        done.epoch == 1
        and [e.kind for e in w.stores["core"].events(RUN)].count(K.RUN_MIGRATED) == 1
    )
    assert finish(w, "core", 2) == control(2)


def test_two_handovers_of_the_same_run_cannot_be_in_flight() -> None:
    import sqlite3

    w = world()
    original = start(w, "core")
    with pytest.raises(Crash):
        move(plan_for("core", "svc1", original), *faulty(w, Fault(2, "before")))
    with pytest.raises(sqlite3.IntegrityError):
        move(plan_for("core", "svc2", original), *clean(w))
    assert w.owners["core"].ownership(RUN).epoch == 0


def test_a_history_that_grew_after_the_check_stops_the_move_before_anything_is_changed() -> None:
    from flowstat.repo.ownership import RunChangedError

    w = world()
    original = start(w, "core")
    w.stores["core"].append(
        RUN,
        [
            Event(
                original[-1].seq + 1,
                K.STEP_FINISHED,
                9.0,
                step="b",
                state=StepState.SUCCEEDED,
                output={},
            )
        ],
    )
    with pytest.raises(RunChangedError):
        move(plan_for("core", "svc1", original), *clean(w))
    recover_all(*clean(w))  # the journal row is abandoned
    assert w.journal.pending() == []
    assert w.owners["core"].ownership(RUN).owned and w.owners["core"].ownership(RUN).epoch == 0
    with pytest.raises(LookupError):
        w.stores["svc1"].get(RUN)


def test_an_owner_that_cannot_be_reached_leaves_the_plan_for_later() -> None:
    from flowstat.migration.handover import OwnerUnavailableError

    w = world()
    original = start(w, "svc1")
    with pytest.raises(Crash):
        move(plan_for("svc1", "svc2", original), *faulty(w, Fault(4, "before")))  # before adopt

    class Down:
        def __getattr__(self, name: str) -> object:
            def call(*args: object) -> object:
                raise OwnerUnavailableError("svc2 does not answer")

            return call

    owners, journal, mirror = clean(w)
    owners["svc2"] = Down()  # type: ignore[assignment]
    assert recover_all(owners, journal, mirror) == {RUN: "unreachable"}
    assert len(w.journal.pending()) == 1
    recover_all(*clean(w))  # it comes back
    assert_sound(w, "svc1", "svc2", original)


def test_the_baseline_stop_and_restart_keeps_no_run() -> None:
    """What the handover is measured against: stopping the old service and starting the run again
    on the new version redoes the steps that had finished (here: a, and b's wait)."""
    original = waiting_history()
    fresh = simulate(new_plan(), INPUTS, 1, behavior).scheduler.events
    redone = [e.step for e in fresh if e.kind is K.STEP_STARTED and e.step in {"a", "b"}]
    kept = [e.step for e in original if e.kind is K.STEP_FINISHED]
    assert redone == ["a", "b"] and kept == ["a"]
