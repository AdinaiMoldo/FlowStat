"""A long chain of guarded steps is analysed, not crashed on (US-43, C2).

The run condition of a step holds the run conditions of the steps it needs, so in a chain in which
every step is guarded by the one before it the condition of step n has n guards inside it, nested
n deep. Past a few hundred steps the analysis ran out of stack (a ``RecursionError``, a 500 of the
API when a draft was saved), and before that its time grew with the square of the length. A run
condition of more than ``RUN_CAP`` nodes is now given up: it becomes an opaque atom, which the
analysis reads as "may or may not run" and so cannot promise anything with it.
"""

from __future__ import annotations

import random
import time

import pytest

from flowstat.analysis import availability, guards, runs
from flowstat.analysis.critical import priorities
from flowstat.analysis.runs import bounds, run_conditions
from flowstat.analysis.types import check_types
from flowstat.analysis.variables import lower_guards
from flowstat.solver.predicate import FALSE, And, Not, Opaque, Pred, holds
from tests.reference.interp import FAILED, SUCCEEDED, run
from tests.unit.analysis.helpers import SIGNATURES, compile_plan, random_workflow
from tests.unit.analysis.test_availability import _random_values


def chain(n: int) -> str:
    """Step i needs step i-1 and runs only if its output says so."""
    out = ["  s0:\n    action: x.y\n"]
    for i in range(1, n):
        out.append(
            f"  s{i}:\n    action: x.y\n    needs: [s{i - 1}]\n"
            f'    when: "${{s{i - 1}.ok}} == true"\n'
        )
    return "".join(out)


def deep(n: int):  # type: ignore[no-untyped-def]
    return compile_plan(chain(n), f"s{n - 1}", inputs="")


@pytest.mark.parametrize("n", [450, 1000])
def test_the_availability_of_a_long_chain_is_analysed(n: int) -> None:
    assert isinstance(availability.analyse(deep(n), SIGNATURES), list)


def test_the_time_of_a_long_chain_stays_far_from_quadratic() -> None:
    """A loose bound: CI runs this under coverage, on slow runners. The figures are measured by
    ``benchmarks/bench_chain.py`` (1,000 steps, 5,000 lines: 1.9 s) and kept in
    ``docs/research/deep-chains.md``; this only catches a return of the blow-up."""
    plan = deep(250)
    started = time.perf_counter()
    availability.analyse(plan, SIGNATURES)
    assert time.perf_counter() - started < 10.0  # 0.2 s on the development machine


def test_every_analysis_that_a_save_makes_survives_a_long_chain() -> None:
    plan = deep(1000)
    assert isinstance(check_types(plan, SIGNATURES), list)
    assert isinstance(guards.analyse(plan, SIGNATURES), list)
    assert isinstance(availability.analyse(plan, SIGNATURES), list)
    assert priorities(plan, SIGNATURES).priority["s0"] >= 1.0


def test_the_run_condition_of_a_step_far_down_the_chain_is_bounded_and_not_exact() -> None:
    plan = deep(400)
    conditions = run_conditions(plan, lower_guards(plan, SIGNATURES).preds)
    last = conditions["s399"]
    assert runs.within(last, runs.RUN_CAP)
    low, _high = bounds(last)
    assert low == FALSE  # nothing is promised about a step whose condition was given up


def test_within_counts_nodes_without_recursing() -> None:
    p: Pred = Opaque("x")
    for _ in range(5000):
        p = And((p, Opaque("y")))
    assert not runs.within(p, 200)
    assert runs.within(And((Opaque("a"), Not(Opaque("b")))), 4)
    assert not runs.within(And((Opaque("a"), Not(Opaque("b")))), 3)


def test_the_run_condition_of_the_first_steps_is_still_exact() -> None:
    plan = deep(400)
    conditions = run_conditions(plan, lower_guards(plan, SIGNATURES).preds)
    assert not isinstance(conditions["s5"], Opaque)
    assert runs.size(conditions["s5"]) <= runs.RUN_CAP


def test_a_workflow_that_is_not_long_is_not_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = deep(20)
    preds = lower_guards(plan, SIGNATURES).preds
    capped = run_conditions(plan, preds)
    monkeypatch.setattr(runs, "RUN_CAP", 10**9)
    assert capped == run_conditions(plan, preds)


@pytest.mark.parametrize("seed", range(3))
def test_giving_a_condition_up_never_makes_the_analysis_promise_too_much(
    seed: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The soundness test of C2 again, with a cap so small that conditions are given up often."""
    monkeypatch.setattr(runs, "RUN_CAP", 3)
    rng = random.Random(700 + seed)
    given_up = checked = 0
    for _ in range(80):
        plan = random_workflow(rng, continue_chance=0.4)
        lowered = lower_guards(plan, SIGNATURES)
        result = availability.compute(plan, lowered.preds)
        with monkeypatch.context() as exact:
            exact.setattr(runs, "RUN_CAP", 10**9)
            whole = run_conditions(plan, lowered.preds)
        given_up += sum(result.runs[s] != whole[s] for s in whole)
        flaky = [s.id for s in plan.steps if s.on_failure == "continue"]
        for _ in range(6):
            values = _random_values(rng)
            failing = frozenset(f for f in flaky if rng.random() < 0.5)
            states = run(plan, values, failing)
            for y, state in states.items():
                if not (state == SUCCEEDED or (state == FAILED and y in failing)):
                    continue
                for x, fact in result.guaranteed[y].items():
                    if holds(fact, values) is True:
                        checked += 1
                        assert states[x] == SUCCEEDED, (plan.name, y, x, values, failing)
    assert given_up > 20 and checked > 300  # the cap was in force, and facts were still checked
