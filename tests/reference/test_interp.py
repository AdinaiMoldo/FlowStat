"""Tests of the reference interpreter itself, and of the engine against it.

The first part checks the interpreter against a table of examples worked out by hand from the
specification (spec 5 and 5.1), so that it does not agree with the engine by construction. The
second part compares the engine with it on random workflows, with steps that fail.
"""

from __future__ import annotations

import random

import pytest

from flowstat.engine.scheduler import Start
from flowstat.engine.simulation import Done, Fail, Result, simulate
from flowstat.model.states import StepState
from tests.reference.interp import (
    FAILED,
    SKIPPED,
    SUCCEEDED,
    UPSTREAM_FAILED,
    ancestors,
    executions,
    join_result,
    longest_chain,
    run,
)
from tests.unit.analysis.helpers import compile_plan, random_workflow, step

S, F, K, U = SUCCEEDED, FAILED, SKIPPED, UPSTREAM_FAILED

# (rule, k, states of the dependencies, what the step does: None = it is decided by its guard)
JOIN_TABLE = [
    ("all_success", 0, [S, S], None),
    ("all_success", 0, [S, K], K),
    ("all_success", 0, [S, F], U),
    ("all_success", 0, [K, F], U),
    ("all_success", 0, [U, S], U),
    ("all_success", 0, [K, K], K),
    ("any_success", 0, [S, K], None),
    ("any_success", 0, [F, S], None),
    ("any_success", 0, [K, K], K),
    ("any_success", 0, [F, K], U),
    ("any_success", 0, [U, K], U),
    ("all_done", 0, [F, K], None),
    ("all_done", 0, [U, U], None),
    ("at_least", 2, [S, S, F], None),
    ("at_least", 2, [S, F, K], U),  # one success, one failure: the failure could have filled it
    ("at_least", 2, [S, K, K], K),
    ("at_least", 2, [F, F, F], U),
    ("at_least", 2, [K, K, F], K),  # 0 + 1 failure is still below 2
    ("at_least", 3, [S, S, F], U),
    ("at_least", 1, [K, K], K),
]


@pytest.mark.parametrize(("kind", "k", "deps", "expected"), JOIN_TABLE)
def test_the_join_table(kind: str, k: int, deps: list[str], expected: str | None) -> None:
    assert join_result(kind, k, deps) == expected


def test_a_guard_decides_after_the_join() -> None:
    plan = compile_plan(
        step("a") + step("b", "${inputs.flag}", ["a"]) + step("c", None, ["b"]), "c"
    )
    assert run(plan, {"inputs.flag": True}) == {"a": S, "b": S, "c": S}
    assert run(plan, {"inputs.flag": False}) == {"a": S, "b": K, "c": K}
    assert run(plan, {"inputs.flag": True}, frozenset({"b"})) == {"a": S, "b": F, "c": U}


def test_a_guard_that_reads_a_step_without_output_fails_the_step() -> None:
    plan = compile_plan(
        step("a", "${inputs.flag}", action="stats.aggregate")
        + step("b", "${a.regions} > 0", ["a"], "all_done"),
        "b",
    )
    values = {"inputs.flag": False, "a.regions": 3}
    assert run(plan, values) == {"a": K, "b": F}
    assert run(plan, {**values, "inputs.flag": True}) == {"a": S, "b": S}


@pytest.mark.parametrize(
    ("guard", "values", "expected"),
    [
        ("${inputs.amount} > 5", {"inputs.amount": 6}, S),
        ("${inputs.amount} > 5", {"inputs.amount": 5}, K),
        ("${inputs.mode} in ['fast', 'slow']", {"inputs.mode": "off"}, K),
        ("${inputs.mode} in ['fast', 'slow']", {"inputs.mode": "slow"}, S),
        ("${inputs.flag} == 1", {"inputs.flag": True}, K),  # true is not 1
        ("not ${inputs.flag}", {"inputs.flag": False}, S),
        ("${inputs.amount} / 0 > 1", {"inputs.amount": 4}, F),  # an error fails the step
        ("${inputs.amount} > 5 or ${inputs.amount} / 0 > 1", {"inputs.amount": 9}, S),  # short cut
        ("${inputs.mode} < 3", {"inputs.mode": "fast"}, F),  # ordering needs the same kind
        ("${inputs.ratio} * 2 >= 1", {"inputs.ratio": 0.5}, S),
        ("${inputs.amount} % 2 == 0", {"inputs.amount": 4}, S),
    ],
)
def test_the_meaning_of_a_guard(guard: str, values: dict[str, object], expected: str) -> None:
    plan = compile_plan(step("a", guard), "a")
    assert run(plan, values) == {"a": expected}


def test_the_enumeration_covers_every_combination() -> None:
    plan = compile_plan(
        step("a", None, extra="    on_failure: continue\n") + step("b", "${inputs.flag}", ["a"]),
        "b",
    )
    seen = list(executions(plan, {"inputs.flag": [True, False]}))
    assert len(seen) == 4  # two values, a may or may not fail
    assert {(e.values["inputs.flag"], e.failing == frozenset({"a"})) for e in seen} == {
        (True, False),
        (True, True),
        (False, False),
        (False, True),
    }
    assert len(list(executions(plan, {"inputs.flag": [True]}, failures=False))) == 1


def test_only_steps_that_continue_may_fail() -> None:
    plan = compile_plan(step("a") + step("b", None, ["a"]), "b")
    assert [e.failing for e in executions(plan, {"inputs.flag": [True]})] == [frozenset()]


def test_the_longest_chain_that_ran() -> None:
    """A (1 s), B (10 s, x > 5), C (10 s, all_done, x < 3): the example of the Proposal."""
    plan = compile_plan(
        step("A")
        + step("B", "${inputs.amount} > 5", ["A"])
        + step("C", "${inputs.amount} < 3", ["B"], "all_done"),
        "C",
    )
    durations = {"A": 1.0, "B": 10.0, "C": 10.0}
    lengths = {}
    for amount in (0, 2, 4, 6, 9):
        states = run(plan, {"inputs.amount": amount})
        ran = {s for s, v in states.items() if v == S}
        lengths[amount] = longest_chain(plan, ran, durations)
    assert lengths == {0: 11.0, 2: 11.0, 4: 1.0, 6: 11.0, 9: 11.0}  # never 21
    assert ancestors(plan)["C"] == {"A", "B"}


def test_a_chain_may_jump_over_a_skipped_step() -> None:
    plan = compile_plan(
        step("A") + step("B", "${inputs.flag}", ["A"]) + step("C", None, ["B"], "all_done"), "C"
    )
    ran = {"A", "C"}
    assert longest_chain(plan, ran, {"A": 2.0, "B": 7.0, "C": 3.0}) == 5.0


# -- the engine against the reference ---------------------------------------------------------
def _random_values(rng: random.Random) -> dict[str, object]:
    return {
        "inputs.amount": rng.randint(0, 100),
        "inputs.mode": rng.choice(["fast", "slow", "off"]),
        "inputs.ratio": rng.choice([0.0, 0.25, 0.5, 0.75, 1.0]),
        "inputs.flag": rng.random() < 0.5,
    }


@pytest.mark.parametrize("seed", range(4))
def test_the_engine_agrees_with_the_reference(seed: int) -> None:
    rng = random.Random(1000 + seed)
    compared = failed = upstream = 0
    for _ in range(120):
        plan = random_workflow(rng, continue_chance=0.35)
        flaky = [s.id for s in plan.steps if s.on_failure == "continue"]
        for _ in range(4):
            values = _random_values(rng)
            failing = frozenset(f for f in flaky if rng.random() < 0.4)
            inputs = {k.split(".", 1)[1]: v for k, v in values.items()}

            def behavior(start: Start, failing: frozenset[str] = failing) -> Result:
                return Fail() if start.step in failing else Done()

            sim = simulate(plan, inputs, max_parallel=3, behavior=behavior)
            want = run(plan, values, failing)
            for step_id, state in want.items():
                got = sim.scheduler.states[step_id]
                assert got is StepState[state], (plan.name, step_id, state, got, values, failing)
                compared += 1
                failed += state == FAILED
                upstream += state == UPSTREAM_FAILED
    assert compared > 1500 and failed > 100 and upstream > 50  # failures are well represented
