"""Tests of the prediction of a run (C6): which steps run and which are skipped for given values.

The prediction applies the four join rules to the truth of the guards. It is checked against the
engine itself: for random workflows and random values, every step that the prediction calls run or
skipped must end SUCCEEDED or SKIPPED in a simulated run with actions that always succeed.
"""

from __future__ import annotations

import random

import pytest

from flowstat.analysis.predict import RUN, SKIP, UNKNOWN, predict
from flowstat.analysis.variables import lower_guards
from flowstat.engine.simulation import simulate
from flowstat.model.states import StepState
from tests.unit.analysis.helpers import SIGNATURES, compile_plan, random_workflow, step

VALUES: dict[str, object] = {
    "inputs.amount": 50,
    "inputs.mode": "fast",
    "inputs.ratio": 0.5,
    "inputs.flag": True,
}


def states(steps: str, goal: str, **changes: object) -> dict[str, str]:
    plan = compile_plan(steps, goal)
    guards = lower_guards(plan, SIGNATURES)
    return predict(plan, guards.preds, {**VALUES, **changes})


def test_a_guard_decides_its_step() -> None:
    steps = step("a", "${inputs.amount} > 10")
    assert states(steps, "a") == {"a": RUN}
    assert states(steps, "a", **{"inputs.amount": 3}) == {"a": SKIP}


def test_a_step_without_a_guard_runs() -> None:
    assert states(step("a"), "a") == {"a": RUN}


def test_all_success_skips_when_a_dependency_is_skipped() -> None:
    steps = step("a", "${inputs.flag}") + step("b", None, ["a"])
    assert states(steps, "b") == {"a": RUN, "b": RUN}
    assert states(steps, "b", **{"inputs.flag": False}) == {"a": SKIP, "b": SKIP}


def test_any_success() -> None:
    steps = (
        step("a", "${inputs.flag}")
        + step("b", "${inputs.amount} > 10")
        + step("c", None, ["a", "b"], "any_success")
    )
    assert states(steps, "c")["c"] == RUN
    assert states(steps, "c", **{"inputs.flag": False})["c"] == RUN  # b still runs
    assert states(steps, "c", **{"inputs.flag": False, "inputs.amount": 1})["c"] == SKIP


def test_all_done_runs_whatever_the_dependencies_did() -> None:
    steps = step("a", "${inputs.flag}") + step("b", None, ["a"], "all_done")
    assert states(steps, "b", **{"inputs.flag": False}) == {"a": SKIP, "b": RUN}


def test_at_least() -> None:
    steps = (
        step("a", "${inputs.flag}")
        + step("b", "${inputs.amount} > 10")
        + step("c", "${inputs.mode} == 'fast'")
        + step("d", None, ["a", "b", "c"], "{at_least: 2}")
    )
    assert states(steps, "d")["d"] == RUN
    assert states(steps, "d", **{"inputs.flag": False})["d"] == RUN  # two of three
    assert states(steps, "d", **{"inputs.flag": False, "inputs.amount": 1})["d"] == SKIP


def test_the_guard_of_a_step_that_passed_its_join_still_applies() -> None:
    steps = step("a") + step("b", "${inputs.flag}", ["a"])
    assert states(steps, "b", **{"inputs.flag": False}) == {"a": RUN, "b": SKIP}


def test_a_guard_outside_the_fragment_is_unknown_and_so_is_what_depends_on_it() -> None:
    steps = (
        step("a", "len('x') > 0")
        + step("b", None, ["a"])
        + step("c", None, ["a"], "all_done")
        + step("d", None, ["a", "x"], "any_success")
        + step("x")
    )
    got = states(steps, "b")
    assert got["a"] == UNKNOWN and got["b"] == UNKNOWN
    assert got["c"] == RUN  # all_done does not care
    assert got["d"] == RUN  # x ran, so any_success holds


def test_a_quorum_that_depends_on_an_unknown_step() -> None:
    steps = (
        step("a", "len('x') > 0")
        + step("b")
        + step("c")
        + step("d", None, ["a", "b", "c"], "{at_least: 2}")
        + step("e", None, ["a", "b", "c"], "{at_least: 3}")
    )
    got = states(steps, "d")
    assert got["d"] == RUN  # b and c are enough
    assert got["e"] == UNKNOWN  # depends on a


def test_a_guard_that_reads_a_step_that_did_not_run_is_unknown() -> None:
    steps = step("a", "${inputs.flag}", action="stats.aggregate") + step(
        "b", "${a.regions} > 0", ["a"], "all_done"
    )
    assert states(steps, "b", **{"a.regions": 5})["b"] == RUN
    assert states(steps, "b", **{"a.regions": 5, "inputs.flag": False})["b"] == UNKNOWN


def test_a_foreach_step_uses_its_instances() -> None:
    steps = (
        "  load:\n    foreach: region in catalogue.regions\n    action: x.y\n"
        '  after:\n    action: x.y\n    needs: ["load[*]"]\n    join: {at_least: 2}\n'
    )
    got = states(steps, "after")
    assert got == {"load[A]": RUN, "load[B]": RUN, "after": RUN}


# -- against the engine -----------------------------------------------------------------------
def _random_values(rng: random.Random) -> dict[str, object]:
    return {
        "inputs.amount": rng.randint(0, 100),
        "inputs.mode": rng.choice(["fast", "slow", "off"]),
        "inputs.ratio": rng.choice([0.0, 0.25, 0.5, 0.75, 1.0]),
        "inputs.flag": rng.random() < 0.5,
    }


@pytest.mark.parametrize("seed", range(4))
def test_the_prediction_agrees_with_the_engine(seed: int) -> None:
    rng = random.Random(seed)
    checked = ran = skipped = 0
    for _ in range(120):
        plan = random_workflow(rng)
        preds = lower_guards(plan, SIGNATURES).preds
        for _ in range(4):
            values = _random_values(rng)
            inputs = {k.split(".", 1)[1]: v for k, v in values.items()}
            sim = simulate(plan, inputs, max_parallel=3)
            got = predict(plan, preds, values)
            for step_id, label in got.items():
                state = sim.scheduler.states[step_id]
                assert label in (RUN, SKIP), (step_id, label)  # the guards are all clean
                assert state is (StepState.SUCCEEDED if label == RUN else StepState.SKIPPED), (
                    plan.name,
                    step_id,
                    label,
                    state,
                    values,
                )
                checked += 1
                ran += label == RUN
                skipped += label == SKIP
    assert checked > 1000 and ran > 300 and skipped > 300  # both outcomes are well represented
