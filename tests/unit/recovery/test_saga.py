"""Tests of the compensation planner (C4, level 2): who must be undone before whom."""

from __future__ import annotations

import random

import pytest
from benchmarks.bench_priority import random_workflow

from flowstat.compiler.plan import Plan
from flowstat.recovery.saga import compensation_blockers
from tests.unit.engine.helpers import REGIONS, doc, example_plan, plan_of


def plan(steps: str) -> Plan:
    return plan_of(doc(steps, "goals: {any_of: [z]}\n"))


CHAIN = (
    "  a:\n    action: x.y\n  b:\n    action: x.y\n    needs: [a]\n"
    "  c:\n    action: x.y\n    needs: [b]\n  z:\n    action: x.y\n    needs: [c]\n"
)


def test_in_a_chain_every_step_waits_for_all_that_come_after_it() -> None:
    blockers = compensation_blockers(plan(CHAIN), ["a", "b", "c"])
    assert blockers == {"a": frozenset({"b", "c"}), "b": frozenset({"c"}), "c": frozenset()}


def test_a_step_that_is_not_undone_does_not_appear_but_still_links_its_neighbours() -> None:
    blockers = compensation_blockers(plan(CHAIN), ["a", "c"])  # b has no compensation
    assert blockers == {"a": frozenset({"c"}), "c": frozenset()}


def test_independent_steps_do_not_wait_for_each_other() -> None:
    steps = (
        "  a:\n    action: x.y\n  b:\n    action: x.y\n  z:\n    action: x.y\n    needs: [a, b]\n"
    )
    assert compensation_blockers(plan(steps), ["a", "b"]) == {
        "a": frozenset(),
        "b": frozenset(),
    }


def test_a_diamond() -> None:
    steps = (
        "  a:\n    action: x.y\n  b:\n    action: x.y\n    needs: [a]\n"
        "  c:\n    action: x.y\n    needs: [a]\n  z:\n    action: x.y\n    needs: [b, c]\n"
    )
    blockers = compensation_blockers(plan(steps), ["a", "b", "c", "z"])
    assert blockers["a"] == frozenset({"b", "c", "z"})
    assert blockers["b"] == blockers["c"] == frozenset({"z"})
    assert blockers["z"] == frozenset()


def test_nothing_to_undo_is_an_empty_plan() -> None:
    assert compensation_blockers(plan(CHAIN), []) == {}


def test_instances_of_a_foreach_step_wait_for_what_consumes_them() -> None:
    undo = ["consolidate", *(f"check[{r}]" for r in REGIONS), *(f"load[{r}]" for r in REGIONS)]
    blockers = compensation_blockers(example_plan(), undo)
    assert blockers["consolidate"] == frozenset()
    assert blockers["check[BIS]"] == frozenset({"consolidate"})
    assert blockers["load[BIS]"] == frozenset({"check[BIS]", "consolidate"})


def test_an_unknown_step_is_refused() -> None:
    with pytest.raises(KeyError):
        compensation_blockers(plan(CHAIN), ["nope"])


# -- oracle: a naive search for the descendants of each step --------------------------------------
def naive(plan_: Plan, undo: list[str]) -> dict[str, frozenset[str]]:
    followers: dict[str, list[str]] = {s.id: [] for s in plan_.steps}
    for s in plan_.steps:
        for p in s.needs:
            followers[p].append(s.id)
    result = {}
    for u in undo:
        seen: set[str] = set()
        stack = list(followers[u])
        while stack:
            x = stack.pop()
            if x not in seen:
                seen.add(x)
                stack.extend(followers[x])
        result[u] = frozenset(seen & set(undo))
    return result


@pytest.mark.property
@pytest.mark.parametrize("seed", range(40))
def test_the_bitset_planner_agrees_with_a_naive_search_on_random_dags(seed: int) -> None:
    rng = random.Random(seed)
    plan_, _ = random_workflow(rng, rng.choice([5, 20, 60]))
    undo = [s.id for s in plan_.steps if rng.random() < 0.6]
    assert compensation_blockers(plan_, undo) == naive(plan_, undo)
