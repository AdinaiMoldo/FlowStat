"""Crash injection (NFR-02): the engine dies after every event of a run and is restarted.

For the nine-region example and for sagas, the history of an uninterrupted run is cut after each
of its events (the moment of the crash), the run is restored from the cut and finished, and the
result is compared with the uninterrupted run:

* the run ends in the same outcome and every step in the same state;
* every call that had been made was made under the same key as in the uninterrupted run, so with
  idempotent actions every effect exists exactly once; no step that had finished is called again;
* the history continues the stored one without a gap, and the report folded from it agrees with
  the scheduler;
* a step that was running and is not idempotent is never called again, it waits for a person.

The same is done for two crashes in a row (a crash during the recovery).
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping

import pytest

from flowstat.actions.registry import default_registry
from flowstat.compiler.plan import Plan
from flowstat.engine.scheduler import Start
from flowstat.engine.simulation import Done, Fail, Result, Simulation, Wait, simulate
from flowstat.model.events import Event, EventKind
from flowstat.model.history import report_from
from flowstat.model.states import RunOutcome, StepState
from flowstat.recovery.saga import compensation_blockers
from tests.unit.engine.helpers import doc, example_plan, plan_of
from tests.unit.engine.test_compensation import random_saga

K = EventKind
S = StepState
Key = tuple[str, int, bool]  # step, attempt, is an undo: the idempotency key of a call
Behavior = Callable[[Start], Result]

INPUTS = {"quarter": "2025Q1"}
ROWS = [{"code": "I01", "name": "Population", "value": 100.0}]


# -- the harness -------------------------------------------------------------------------------
def keys_of_events(events: list[Event]) -> set[Key]:
    """The calls that the history says were dispatched (a dispatched call may have run)."""
    keys: set[Key] = set()
    undoing: set[str] = set()
    for e in events:
        if e.step is None:
            continue
        if e.kind is K.STEP_STARTED:
            keys.add((e.step, 1, False))
        elif e.kind is K.STEP_COMPENSATING:
            undoing.add(e.step)
            keys.add((e.step, e.attempt or 1, True))
        elif e.kind is K.STEP_RETRY and e.attempt is not None:
            keys.add((e.step, e.attempt, e.step in undoing))
    return keys


def keys_of_calls(calls: list[Start]) -> set[Key]:
    return {(c.step, c.attempt, c.compensation) for c in calls}


def settled(events: list[Event]) -> set[str]:
    """Steps that had a final state in the history (not an undo)."""
    return {
        e.step
        for e in events
        if e.kind is K.STEP_FINISHED
        and e.step is not None
        and e.state not in (S.COMPENSATED, S.COMPENSATION_FAILED)
    }


def finish(
    plan: Plan,
    prefix: list[Event],
    behavior: Behavior,
    workers: int,
    answers: Mapping[str, tuple[float, Mapping[str, object]]] | None = None,
    idempotent: Callable[[str], bool] = lambda action: True,
) -> Simulation:
    return simulate(
        plan,
        INPUTS,
        workers,
        behavior,
        answers=answers,
        compensation_planner=compensation_blockers,
        resume=prefix,
        idempotent=idempotent,
    )


def check_order(plan: Plan, events: list[Event]) -> None:
    """An undo starts only after the undos of everything that depends on its step."""
    succeeded = {e.step for e in events if e.kind is K.STEP_FINISHED and e.state is S.SUCCEEDED}
    to_undo = [s.id for s in plan.steps if s.compensate is not None and s.id in succeeded]
    blockers = compensation_blockers(plan, to_undo)
    done_at = {
        e.step: e.seq for e in events if e.kind is K.STEP_FINISHED and e.state is S.COMPENSATED
    }
    for e in events:
        if e.kind is K.STEP_COMPENSATING and e.step is not None:
            for b in blockers[e.step]:
                assert b in done_at and done_at[b] < e.seq, f"{e.step} was undone before {b}"


def check_same_run(
    plan: Plan,
    reference: Simulation,
    prefix: list[Event],
    resumed: Simulation,
    where: str,
    same_states: bool = True,
) -> None:
    """What every crash must leave true."""
    after = resumed.scheduler
    events = after.events
    assert [e.seq for e in events] == list(range(len(events))), where
    assert events[: len(prefix)] == prefix, where
    # the same calls, under the same keys; nothing that had settled is called again
    made = keys_of_events(prefix) | keys_of_calls(resumed.calls)
    assert made == keys_of_calls(reference.calls), where
    again = {c.step for c in resumed.calls if not c.compensation}
    assert not (again & settled(prefix)), where
    # the same result
    assert after.outcome is reference.scheduler.outcome, where
    if same_states:
        assert after.states == reference.scheduler.states, where
        assert after.outputs == reference.scheduler.outputs, where
    # the report agrees with the scheduler
    report = report_from("r", "w", 1, events, [s.id for s in plan.steps])
    assert {s.step: s.state for s in report.steps} == after.states, where
    check_order(plan, events)


# -- the nine-region example -------------------------------------------------------------------
def example_behavior(retry_publish: int = 2) -> Behavior:
    def act(command: Start) -> Result:
        step = command.step
        if step.startswith("load"):
            return Done(1.0, {"rows": ROWS, "count": 1})
        if step.startswith("check"):
            return Done(1.0, {"rows": ROWS, "checked": 1})
        if step == "consolidate":
            return Done(2.0, {"rows": ROWS, "regions": 9})
        if step == "approve":
            return Wait(0.5)
        if step == "publish":
            if command.attempt <= retry_publish:
                return Fail(1.0, "E-ACT-008", "lost connection", transient=True)
            return Done(1.0, {"written": 1, "batch": "r"})
        return Done(1.0, {"queued": 1})

    return act


APPROVED = {"approve": (1.0, {"decision": "approved"})}
REJECTED = {"approve": (1.0, {"decision": "rejected"})}
WORKERS = 4


def example_reference(answers: Mapping[str, tuple[float, Mapping[str, object]]]) -> Simulation:
    return simulate(
        example_plan(),
        INPUTS,
        WORKERS,
        example_behavior(),
        answers=answers,
        compensation_planner=compensation_blockers,
    )


APPROVED_RUN = example_reference(APPROVED)
REJECTED_RUN = example_reference(REJECTED)
N_APPROVED = len(APPROVED_RUN.scheduler.events)
N_REJECTED = len(REJECTED_RUN.scheduler.events)


def test_the_uninterrupted_runs_are_what_the_crashes_are_compared_with() -> None:
    assert APPROVED_RUN.scheduler.outcome is RunOutcome.SUCCEEDED
    assert REJECTED_RUN.scheduler.outcome is RunOutcome.SUCCEEDED
    assert N_APPROVED > 40 and N_REJECTED > 40  # nine regions, two phases, approval, a retry


@pytest.mark.parametrize("cut", range(N_APPROVED + 1))
def test_the_approved_example_survives_a_crash_after_every_event(cut: int) -> None:
    prefix = APPROVED_RUN.scheduler.events[:cut]

    resumed = finish(example_plan(), prefix, example_behavior(), WORKERS, APPROVED)

    check_same_run(example_plan(), APPROVED_RUN, prefix, resumed, f"crash after event {cut}")


@pytest.mark.parametrize("cut", range(0, N_REJECTED + 1, 2))
def test_the_rejected_example_never_repeats_the_mail_after_a_crash(cut: int) -> None:
    plan = example_plan()
    registry = default_registry()
    prefix = REJECTED_RUN.scheduler.events[:cut]

    resumed = finish(
        plan, prefix, example_behavior(), WORKERS, REJECTED, idempotent=registry.is_idempotent
    )

    mail_started = any(e.kind is K.STEP_STARTED and e.step == "return_to_regions" for e in prefix)
    mail_done = "return_to_regions" in settled(prefix)
    after = resumed.scheduler
    if mail_started and not mail_done:
        # the mail may have been sent: it is not sent again, a person decides
        assert [c for c in resumed.calls if c.step == "return_to_regions"] == []
        assert after.states["return_to_regions"] is S.UNCERTAIN
        assert after.outcome is None and after.status == "WAITING"
        assert after.states["publish"] is S.SKIPPED  # everything else was done
        after.resolve("return_to_regions", "done", resumed.makespan + 1, {"queued": 1})
        assert after.outcome is RunOutcome.SUCCEEDED
    else:
        check_same_run(plan, REJECTED_RUN, prefix, resumed, f"crash after event {cut}")


@pytest.mark.parametrize("first", range(1, N_APPROVED, 4))
def test_a_crash_during_the_recovery_is_survived_too(first: int) -> None:
    plan = example_plan()
    once = finish(
        plan, APPROVED_RUN.scheduler.events[:first], example_behavior(), WORKERS, APPROVED
    )
    stored = once.scheduler.events
    for second in range(first + 1, len(stored), 7):
        prefix = stored[:second]

        resumed = finish(plan, prefix, example_behavior(), WORKERS, APPROVED)

        check_same_run(
            plan, APPROVED_RUN, prefix, resumed, f"crashes after events {first} and {second}"
        )


# -- compensation ------------------------------------------------------------------------------
SAGA = (
    "  a:\n    action: x.y\n    compensate: x.undo_a\n"
    "  b:\n    action: x.y\n    needs: [a]\n    compensate: x.undo_b\n"
    "  d:\n    action: x.y\n    needs: [a]\n    compensate: x.undo_d\n"
    "  e:\n    action: x.y\n    needs: [b, d]\n    compensate: x.undo_e\n"
    "  f:\n    action: x.y\n    needs: [e]\n    on_failure: compensate\n"
)


def saga_plan() -> Plan:
    return plan_of(doc(SAGA, "goals: {any_of: [f]}\n"))


def saga_behavior(broken_undo: str | None = None) -> Behavior:
    def act(command: Start) -> Result:
        if command.compensation:
            if command.step == broken_undo:
                return Fail(1.0, "E-ACT-002", "the undo broke")
            return Done(1.0 + len(command.step) / 10, {})
        if command.step == "f":
            return Fail(1.0, "E-ACT-002", "f broke")
        return Done(1.0, {})

    return act


def saga_run(workers: int, broken_undo: str | None = None) -> Simulation:
    return simulate(
        saga_plan(),
        {},
        workers,
        saga_behavior(broken_undo),
        compensation_planner=compensation_blockers,
    )


def saga_cuts(workers: int, broken: str | None) -> list[int]:
    return list(range(len(saga_run(workers, broken).scheduler.events) + 1))


@pytest.mark.parametrize("cut", saga_cuts(1, None))
def test_a_compensation_survives_a_crash_after_every_event(cut: int) -> None:
    reference = saga_run(1)
    prefix = reference.scheduler.events[:cut]

    resumed = simulate(
        saga_plan(),
        {},
        1,
        saga_behavior(),
        compensation_planner=compensation_blockers,
        resume=prefix,
    )

    check_same_run(saga_plan(), reference, prefix, resumed, f"crash after event {cut}")
    assert resumed.scheduler.outcome is RunOutcome.COMPENSATED


@pytest.mark.parametrize("cut", saga_cuts(1, "d"))
def test_a_failed_undo_is_the_same_after_a_crash_at_any_event(cut: int) -> None:
    reference = saga_run(1, "d")
    prefix = reference.scheduler.events[:cut]

    resumed = simulate(
        saga_plan(),
        {},
        1,
        saga_behavior("d"),
        compensation_planner=compensation_blockers,
        resume=prefix,
    )

    check_same_run(saga_plan(), reference, prefix, resumed, f"crash after event {cut}")
    assert resumed.scheduler.outcome is RunOutcome.COMPENSATION_FAILED
    assert resumed.scheduler.states["a"] is S.SUCCEEDED  # behind the failed undo of d


@pytest.mark.parametrize("cut", saga_cuts(3, None))
def test_with_parallel_undos_every_crash_still_gives_a_complete_and_ordered_compensation(
    cut: int,
) -> None:
    reference = saga_run(3)
    prefix = reference.scheduler.events[:cut]

    resumed = simulate(
        saga_plan(),
        {},
        3,
        saga_behavior(),
        compensation_planner=compensation_blockers,
        resume=prefix,
    )

    after = resumed.scheduler
    assert after.outcome is RunOutcome.COMPENSATED
    assert [after.states[x] for x in "abde"] == [S.COMPENSATED] * 4
    undo_keys = [(c.step, c.attempt) for c in resumed.calls if c.compensation]
    started_undo = {(k[0], k[1]) for k in keys_of_events(prefix) if k[2]}
    assert not (set(undo_keys) - started_undo) or all(a == 1 for _, a in undo_keys)
    check_order(saga_plan(), after.events)
    assert [e.seq for e in after.events] == list(range(len(after.events)))


# -- random sagas ------------------------------------------------------------------------------
@pytest.mark.property
@pytest.mark.parametrize("seed", range(20))
def test_random_sagas_are_the_same_after_a_crash_at_random_events(seed: int) -> None:
    rng = random.Random(seed)
    plan, failing = random_saga(rng, rng.choice([6, 12, 25]))

    def act(command: Start) -> Result:
        if command.compensation:
            return Done(1.0 + (hash(command.step) % 3), {})
        return Fail(1.0, "E-ACT-002", "x") if command.step == failing else Done(1.0, {})

    reference = simulate(plan, {}, 1, act, compensation_planner=compensation_blockers)
    events = reference.scheduler.events
    for cut in sorted(rng.sample(range(len(events) + 1), min(8, len(events) + 1))):
        prefix = events[:cut]

        resumed = simulate(
            plan, {}, 1, act, compensation_planner=compensation_blockers, resume=prefix
        )

        check_same_run(plan, reference, prefix, resumed, f"seed {seed}, crash after event {cut}")
