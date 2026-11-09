"""Conformance of a recorded history with the new version of the workflow (US-32).

Each case is a pair of versions and a crash point: the history of the old version is cut at an
event and the verdict is checked, reason by reason. The oracle test at the end checks the claim
the verdict makes: when it says "migratable", restoring the history under the new plan and
running the new plan from the start with the recorded results give the same fate to every step.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

import pytest

from flowstat.analysis.signatures import ActionSignature
from flowstat.compiler.plan import Plan
from flowstat.engine.scheduler import Scheduler, Start
from flowstat.engine.simulation import Done, Fail, Result, Wait, simulate
from flowstat.migration.conformance import Verdict, check
from flowstat.model.events import Event, EventKind
from flowstat.model.states import StepState
from flowstat.recovery.saga import compensation_blockers
from tests.unit.engine.helpers import catalogue, doc, plan_of

K = EventKind
S = StepState

INPUTS = "  n: {type: int}\n"
CHAIN = (
    "  a:\n    action: x.y\n"
    "  b:\n    action: x.y\n    needs: [a]\n"
    "  c:\n    action: x.y\n    needs: [b]\n"
)


def plan(steps: str, goal: str = "c", inputs: str = INPUTS) -> Plan:
    return plan_of(doc(steps, f"goals: {{any_of: [{goal}]}}\n", inputs))


def run(p: Plan, behavior: Callable[[Start], Result] | None = None) -> list[Event]:
    sim = simulate(
        p, {"n": 5}, 1, behavior, compensation_planner=compensation_blockers, answers=None
    )
    return sim.scheduler.events


def upto(events: list[Event], kind: EventKind, step: str | None = None) -> list[Event]:
    """The history up to and including the first event of this kind (and step)."""
    for position, e in enumerate(events):
        if e.kind is kind and (step is None or e.step == step):
            return events[: position + 1]
    raise AssertionError(f"no {kind} {step}")


def finished(events: list[Event], step: str) -> list[Event]:
    return upto(events, K.STEP_FINISHED, step)


def verdict(
    old: Plan,
    new: Plan,
    events: list[Event],
    signatures: Mapping[str, ActionSignature] | None = None,
    inputs: Mapping[str, object] | None = None,
) -> Verdict:
    return check(old, new, events, inputs or {"n": 5}, signatures or {}, catalogue())


def codes(v: Verdict) -> list[tuple[str, str | None]]:
    return [(r.code, r.step) for r in v.reasons]


# -- what may change ---------------------------------------------------------------------------
def test_the_same_version_is_migratable_and_nothing_is_new() -> None:
    old = plan(CHAIN)
    v = verdict(old, plan(CHAIN), finished(run(old), "b"))
    assert v.migratable and v.reasons == ()
    assert (v.kept, v.added, v.pending, v.dropped) == (("a", "b"), (), ("c",), ())


def test_a_new_step_at_the_end_is_added_and_pending() -> None:
    old = plan(CHAIN)
    new = plan(CHAIN + "  d:\n    action: x.y\n    needs: [c]\n", goal="d")
    v = verdict(old, new, finished(run(old), "a"))
    assert v.migratable
    assert v.added == ("d",) and v.pending == ("b", "c", "d")


def test_a_new_independent_step_is_pending() -> None:
    old = plan(CHAIN)
    new = plan(CHAIN + "  e:\n    action: x.y\n")
    v = verdict(old, new, finished(run(old), "a"))
    assert v.migratable and "e" in v.added and "e" in v.pending


def test_a_step_that_has_not_run_may_change_in_every_way() -> None:
    old = plan(CHAIN)
    new = plan(
        "  a:\n    action: x.y\n"
        "  b:\n    action: x.y\n    needs: [a]\n"
        "  c:\n    action: x.other\n    needs: [b]\n    with: {k: 1}\n"
        "    retry: {max: 2, backoff: fixed}\n    when: ${inputs.n} > 1\n"
    )
    assert verdict(old, new, finished(run(old), "b")).migratable


def test_a_step_that_has_not_run_may_be_removed_and_is_dropped() -> None:
    old = plan(CHAIN)
    new = plan(CHAIN.split("  c:")[0], goal="b")
    v = verdict(old, new, finished(run(old), "b"))
    assert v.migratable and v.dropped == ("c",)


def test_retry_and_failure_policy_of_a_step_that_succeeded_may_change() -> None:
    old = plan(CHAIN)
    new = plan(
        CHAIN.replace("  a:\n    action: x.y\n", "  a:\n    action: x.y\n    retry: {max: 3}\n")
    )
    assert verdict(old, new, finished(run(old), "b")).migratable
    new = plan(
        CHAIN.replace(
            "  a:\n    action: x.y\n", "  a:\n    action: x.y\n    on_failure: continue\n"
        )
    )
    assert verdict(old, new, finished(run(old), "b")).migratable


def test_a_waiting_step_is_kept() -> None:
    steps = CHAIN.replace("action: x.y\n    needs: [a]", "action: x.ask\n    needs: [a]")
    old = plan(steps)
    events = upto(run(old, lambda s: Wait() if s.action == "x.ask" else Done()), K.STEP_WAITING)
    v = verdict(old, plan(steps), events)
    assert v.migratable and v.kept == ("a", "b")


def test_a_guard_that_still_gives_the_recorded_fate_is_accepted() -> None:
    skipped = "  z:\n    action: x.y\n    needs: [c]\n    when: ${inputs.n} > 9\n"
    old = plan(CHAIN + skipped, goal="c")
    new = plan(CHAIN + skipped.replace("> 9", "> 7"), goal="c")
    assert verdict(old, new, finished(run(old), "c")).migratable is True
    assert verdict(old, new, run(old)[:-1]).migratable is True  # before RUN_FINISHED


# -- what may not ------------------------------------------------------------------------------
def test_a_finished_run_is_never_moved() -> None:
    old = plan(CHAIN)
    v = verdict(old, plan(CHAIN), run(old))
    assert codes(v) == [("E-MIG-001", None)]


def test_a_run_that_undoes_its_work_is_never_moved() -> None:
    saga = (
        "  a:\n    action: x.y\n    compensate: x.undo\n"
        "  b:\n    action: x.y\n    needs: [a]\n    on_failure: compensate\n"
    )
    old = plan(saga, goal="b")
    events = run(old, lambda s: Fail() if s.step == "b" else Done())
    cut = upto(events, K.COMPENSATION_STARTED)
    assert codes(verdict(old, plan(saga, goal="b"), cut)) == [("E-MIG-001", None)]


def test_an_uncertain_step_is_never_moved() -> None:
    old = plan(CHAIN)
    crash = upto(run(old), K.STEP_STARTED, "b")
    scheduler = Scheduler(old, {"n": 5}, 1, compensation_planner=compensation_blockers)
    scheduler.restore(crash, 50.0, lambda action: False)
    v = verdict(old, plan(CHAIN), scheduler.events)
    assert ("E-MIG-001", "b") in codes(v)


def test_a_call_in_flight_is_refused() -> None:
    old = plan(CHAIN)
    v = verdict(old, plan(CHAIN), upto(run(old), K.STEP_STARTED, "b"))
    assert codes(v) == [("E-MIG-002", "b")]


def test_a_step_with_history_cannot_be_removed() -> None:
    old = plan(CHAIN)
    new = plan("  a:\n    action: x.y\n  c:\n    action: x.y\n    needs: [a]\n")
    v = verdict(old, new, finished(run(old), "b"))
    assert ("E-MIG-003", "b") in codes(v)


@pytest.mark.parametrize(
    "edit",
    [
        ("action: x.y\n    needs: [a]", "action: x.z\n    needs: [a]"),
        ("action: x.y\n    needs: [a]", "action: x.y\n    needs: [a]\n    with: {k: 2}"),
        ("action: x.y\n    needs: [a]", "action: x.y\n    needs: [a]\n    ensures: ${b.k} > 0"),
        ("action: x.y\n    needs: [a]", "action: x.y\n    needs: [a]\n    compensate: x.undo"),
    ],
    ids=["action", "with", "ensures", "compensate"],
)
def test_a_step_with_history_cannot_change_what_it_is(edit: tuple[str, str]) -> None:
    old = plan(CHAIN)
    new = plan(CHAIN.replace(*edit))
    v = verdict(old, new, finished(run(old, lambda s: Done(1.0, {"k": 1})), "b"))
    assert codes(v) == [("E-MIG-004", "b")]


def test_the_text_of_an_argument_does_not_matter_only_its_meaning() -> None:
    old = plan(CHAIN.replace("needs: [a]", "needs: [a]\n    with: {k: '${a.v}'}"))
    new = plan(CHAIN.replace("needs: [a]", "needs: [a]\n    with: {k: '${a.v}'}\n\n"))
    events = finished(run(old, lambda s: Done(1.0, {"v": 3})), "b")
    assert verdict(old, new, events).migratable


def test_a_failed_step_keeps_the_policy_it_failed_under() -> None:
    old = plan(
        CHAIN.replace(
            "action: x.y\n    needs: [a]", "action: x.y\n    needs: [a]\n    on_failure: continue"
        )
    )
    new = plan(CHAIN)
    events = run(old, lambda s: Fail() if s.step == "b" else Done())
    cut = finished(events, "b")
    assert codes(verdict(old, new, cut)) == [("E-MIG-004", "b")]


def test_a_new_predecessor_that_has_not_ended_is_refused() -> None:
    old = plan(CHAIN)
    new = plan(
        "  a:\n    action: x.y\n  n:\n    action: x.y\n"
        "  b:\n    action: x.y\n    needs: [a, n]\n"
        "  c:\n    action: x.y\n    needs: [b]\n"
    )
    v = verdict(old, new, finished(run(old), "b"))
    assert codes(v) == [("E-MIG-005", "b")]


def test_a_join_rule_that_would_have_skipped_the_step_is_refused() -> None:
    steps = (
        "  a:\n    action: x.y\n    on_failure: continue\n"
        "  b:\n    action: x.y\n    needs: [a]\n    join: all_done\n"
    )
    old = plan(steps, goal="b")
    new = plan(steps.replace("join: all_done", "join: all_success"), goal="b")
    events = finished(run(old, lambda s: Fail() if s.step == "a" else Done()), "b")
    assert codes(verdict(old, new, events)) == [("E-MIG-005", "b")]


def test_a_step_that_was_skipped_by_its_join_must_still_be() -> None:
    steps = (
        "  a:\n    action: x.y\n    on_failure: continue\n  b:\n    action: x.y\n    needs: [a]\n"
    )
    old = plan(steps, goal="b")
    new = plan(steps.replace("needs: [a]", "needs: [a]\n    join: all_done"), goal="b")
    events = run(old, lambda s: Fail() if s.step == "a" else Done())
    assert codes(verdict(old, new, events[:-1])) == [("E-MIG-005", "b")]


def test_a_new_guard_that_is_true_for_a_skipped_step_is_refused() -> None:
    skipped = "  z:\n    action: x.y\n    needs: [c]\n    when: ${inputs.n} > 9\n"
    old = plan(CHAIN + skipped)
    new = plan(CHAIN + skipped.replace("> 9", "> 1"))
    v = verdict(old, new, finished(run(old), "z"))
    assert v.migratable is False and ("E-MIG-006", "z") in codes(v)


def test_a_new_guard_that_is_false_for_a_step_that_ran_is_refused() -> None:
    old = plan(CHAIN)
    new = plan(CHAIN.replace("needs: [a]", "needs: [a]\n    when: ${inputs.n} > 9"))
    assert codes(verdict(old, new, finished(run(old), "b"))) == [("E-MIG-006", "b")]


def test_a_guard_that_cannot_be_read_on_the_record_is_refused() -> None:
    old = plan(CHAIN)
    new = plan(CHAIN.replace("needs: [a]", "needs: [a]\n    when: ${a.missing} > 0"))
    v = verdict(old, new, finished(run(old, lambda s: Done(1.0, {"k": 1})), "b"))
    assert codes(v) == [("E-MIG-006", "b")]


def test_a_new_consumer_that_reads_a_field_the_record_lacks_is_refused() -> None:
    old = plan(CHAIN)
    new = plan(
        CHAIN.replace(
            "  c:\n    action: x.y", "  c:\n    action: x.y\n    with: {k: '${a.missing}'}"
        )
    )
    v = verdict(old, new, finished(run(old, lambda s: Done(1.0, {"k": 1})), "b"))
    assert codes(v) == [("E-MIG-007", "c")]


def test_a_new_consumer_that_gets_a_value_of_another_type_is_refused() -> None:
    old = plan(CHAIN)
    new = plan(
        CHAIN.replace(
            "  c:\n    action: x.y", "  c:\n    action: x.take\n    with: {amount: '${a.k}'}"
        )
    )
    sig = {"x.take": ActionSignature({}, params={"amount": "int"})}
    events = finished(run(old, lambda s: Done(1.0, {"k": "ten"})), "b")
    assert codes(verdict(old, new, events, sig)) == [("E-MIG-007", "c")]
    assert verdict(
        old, new, finished(run(old, lambda s: Done(1.0, {"k": 10})), "b"), sig
    ).migratable


def test_an_input_the_new_version_refuses_is_reported() -> None:
    old = plan(CHAIN)
    new = plan(CHAIN, inputs="  n: {type: int}\n  extra: {type: string}\n")
    v = verdict(old, new, finished(run(old), "a"))
    assert codes(v) == [("E-MIG-008", "extra")]


def test_new_defaults_are_added_and_removed_inputs_dropped() -> None:
    old = plan(CHAIN)
    new = plan(CHAIN, inputs="  m: {type: int, default: 4}\n")
    v = verdict(old, new, finished(run(old), "a"))
    assert v.migratable and v.inputs == {"m": 4}


def test_every_reason_is_reported_not_only_the_first() -> None:
    old = plan(CHAIN)
    new = plan("  a:\n    action: x.z\n  c:\n    action: x.y\n    needs: [a]\n")
    v = verdict(old, new, finished(run(old), "b"))
    assert {c for c, _ in codes(v)} == {"E-MIG-003", "E-MIG-004"}


# -- the oracle --------------------------------------------------------------------------------
PAIRS: list[tuple[str, str, str]] = [
    ("same", CHAIN, CHAIN),
    ("tail added", CHAIN, CHAIN + "  d:\n    action: x.y\n    needs: [c]\n"),
    ("tail changed", CHAIN, CHAIN.replace("  c:\n    action: x.y", "  c:\n    action: x.w")),
    (
        "branch added",
        CHAIN,
        CHAIN + "  e:\n    action: x.y\n    needs: [a]\n  f:\n    action: x.y\n    needs: [e, c]\n",
    ),
    (
        "guard moved",
        CHAIN + "  z:\n    action: x.y\n    needs: [c]\n    when: ${inputs.n} > 9\n",
        CHAIN + "  z:\n    action: x.y\n    needs: [c]\n    when: ${inputs.n} > 8\n",
    ),
    (
        "join relaxed",
        CHAIN.replace(
            "  b:\n    action: x.y\n", "  b:\n    action: x.y\n    on_failure: continue\n"
        ),
        CHAIN.replace(
            "  b:\n    action: x.y\n", "  b:\n    action: x.y\n    on_failure: continue\n"
        ).replace("  c:\n    action: x.y\n", "  c:\n    action: x.y\n    join: all_done\n"),
    ),
]


def fates(events: list[Event]) -> dict[str, S]:
    out: dict[str, S] = {}
    for e in events:
        if e.step is not None and e.kind is K.STEP_FINISHED and e.state is not None:
            out[e.step] = e.state
    return out


@pytest.mark.parametrize(("name", "old_steps", "new_steps"), PAIRS, ids=[p[0] for p in PAIRS])
@pytest.mark.parametrize("failing", [None, "b"], ids=["ok", "b-fails"])
def test_a_migratable_history_is_explained_by_the_new_plan(
    name: str, old_steps: str, new_steps: str, failing: str | None
) -> None:
    """At every crash point: if the verdict is yes, restore accepts the history and the steps
    that have a fate keep it in a run of the new plan that is given the same results."""
    old, new = plan(old_steps), plan(new_steps, goal="d" if "d:" in new_steps else "c")
    behavior: Callable[[Start], Result] = (
        (lambda s: Fail() if s.step == failing else Done(1.0, {"k": 1}))
        if failing
        else (lambda s: Done(1.0, {"k": 1}))
    )
    events = run(old, behavior)
    asked = 0
    for cut in range(1, len(events) + 1):
        history = events[:cut]
        v = verdict(old, new, history)
        if not v.migratable:
            continue
        asked += 1
        restored = Scheduler(new, {"n": 5}, 1, compensation_planner=compensation_blockers)
        restored.restore(history, 99.0, lambda action: True)  # must not raise
        recorded = fates(history)

        def replay(start: Start, fate: dict[str, S] = recorded) -> Result:
            if fate.get(start.step) is S.FAILED:
                return Fail()
            return Done(1.0, {"k": 1})

        again = simulate(new, {"n": 5}, 1, replay, compensation_planner=compensation_blockers)
        for step, state in recorded.items():
            if step in v.kept:
                assert again.scheduler.states[step] is state, (name, cut, step)
                assert restored.states[step] is state, (name, cut, step)
    assert asked > 0


# -- the plain replay is not enough ------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "old_steps", "new_steps", "cut"),
    [
        (
            "changed action",
            CHAIN,
            CHAIN.replace("  b:\n    action: x.y", "  b:\n    action: x.z"),
            "b",
        ),
        (
            "guard now false",
            CHAIN,
            CHAIN.replace("needs: [a]", "needs: [a]\n    when: ${inputs.n} > 9"),
            "b",
        ),
        (
            "new predecessor",
            CHAIN,
            CHAIN.replace("needs: [a]", "needs: [a, e]") + "  e:\n    action: x.y\n",
            "b",
        ),
    ],
)
def test_restoring_the_history_under_the_new_plan_accepts_what_the_check_refuses(
    name: str, old_steps: str, new_steps: str, cut: str
) -> None:
    """The plain replay (restore, which trusts the recorded states) raises nothing for these
    pairs, and the run would go on as a different workflow than the one that started it."""
    old, new = plan(old_steps), plan(new_steps)
    history = finished(run(old), cut)
    Scheduler(new, {"n": 5}, 1).restore(history, 99.0, lambda action: True)  # no error
    assert verdict(old, new, history).migratable is False, name
