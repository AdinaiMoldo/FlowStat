"""Tests of the availability analysis (C2; US-21, US-22): docs/dsl-spec.md, section 10.

The analysis is checked against the reference interpreter (``tests/reference``), which shares no
code with it: soundness (whenever a fact holds and a step starts, the step it names has
succeeded) and the exactness of the optimistic run (when no action fails, the steps that run are
exactly those whose run condition holds).
"""

from __future__ import annotations

import random

import pytest

from flowstat.actions.registry import default_registry
from flowstat.analysis.availability import analyse, available_at, compute
from flowstat.analysis.signatures import Signatures
from flowstat.analysis.variables import lower_guards
from flowstat.compiler.catalogue import Catalogue
from flowstat.compiler.compile import compile_workflow
from flowstat.model.diagnostics import Diagnostic, Severity
from flowstat.solver.predicate import TRUE, holds
from tests.reference.interp import FAILED, SUCCEEDED, executions, run
from tests.unit.analysis.helpers import SIGNATURES, compile_plan, random_workflow, step

APPROVE = "  approve:\n    action: approval.wait\n    with: {role: chief}\n"


def findings(steps: str, goal: str = "z", signatures: Signatures = SIGNATURES) -> list[Diagnostic]:
    return analyse(compile_plan(steps, goal), signatures)


def codes(items: list[Diagnostic]) -> list[str]:
    return [d.code for d in items]


def only(prefix: str, items: list[Diagnostic]) -> list[Diagnostic]:
    """The findings of one family (the goal of a small workflow is often undecided too)."""
    return [d for d in items if d.code.startswith(prefix)]


# -- the oracle -------------------------------------------------------------------------------
def _random_values(rng: random.Random) -> dict[str, object]:
    return {
        "inputs.amount": rng.choice([0, 25, 26, 50, 51, 75, 100]),
        "inputs.mode": rng.choice(["fast", "slow", "off"]),
        "inputs.ratio": rng.choice([0.0, 0.25, 0.5, 0.75, 1.0]),
        "inputs.flag": rng.random() < 0.5,
    }


@pytest.mark.parametrize("seed", range(4))
def test_a_fact_that_holds_when_a_step_starts_names_a_step_that_succeeded(seed: int) -> None:
    rng = random.Random(500 + seed)
    facts = nontrivial = with_failures = 0
    for _ in range(150):
        plan = random_workflow(rng, continue_chance=0.4)
        guaranteed = compute(plan, lower_guards(plan, SIGNATURES).preds).guaranteed
        flaky = [s.id for s in plan.steps if s.on_failure == "continue"]
        for _ in range(8):
            values = _random_values(rng)
            failing = frozenset(f for f in flaky if rng.random() < 0.5)
            states = run(plan, values, failing)
            for y, state in states.items():
                started = state == SUCCEEDED or (state == FAILED and y in failing)
                if not started:
                    continue
                with_failures += bool(failing)
                for x, fact in guaranteed[y].items():
                    if holds(fact, values) is True:
                        facts += 1
                        nontrivial += fact != TRUE
                        assert states[x] == SUCCEEDED, (plan.name, y, x, values, failing)
    print(facts, nontrivial, with_failures)
    assert facts > 1000 and nontrivial > 200 and with_failures > 500  # not an empty check


@pytest.mark.parametrize("seed", range(4))
def test_the_optimistic_run_is_exact(seed: int) -> None:
    rng = random.Random(900 + seed)
    ran = skipped = 0
    for _ in range(150):
        plan = random_workflow(rng, continue_chance=0.4)
        runs = compute(plan, lower_guards(plan, SIGNATURES).preds).runs
        for _ in range(8):
            values = _random_values(rng)
            states = run(plan, values)  # nothing fails
            for y, state in states.items():
                holds_now = holds(runs[y], values)
                assert holds_now is not None
                assert (state == SUCCEEDED) is holds_now, (plan.name, y, values)
                ran += state == SUCCEEDED
                skipped += state != SUCCEEDED
    assert ran > 500 and skipped > 500


def test_every_execution_of_a_small_diamond_agrees() -> None:
    """The whole grid and every set of failures, for the case the Proposal names."""
    plan = compile_plan(
        step("r", extra="    on_failure: continue\n")
        + step("a", "${inputs.amount} > 50", ["r"])
        + step("b", "${inputs.amount} <= 50", ["r"])
        + step("c", None, ["a", "b"], "any_success"),
        "c",
    )
    result = compute(plan, lower_guards(plan, SIGNATURES).preds)
    assert result.guaranteed["c"]["r"] == result.guaranteed["c"]["r"]  # the fact is kept
    grid = {"inputs.amount": [0, 49, 50, 51, 100]}
    checked = 0
    for e in executions(plan, grid):
        if e.states["c"] != SUCCEEDED:
            continue
        for x, fact in result.guaranteed["c"].items():
            if holds(fact, e.values) is True:
                assert e.states[x] == SUCCEEDED
                checked += 1
    assert checked > 5
    # r is a continue step, yet both branches need it: the join cannot start without it
    assert holds(result.guaranteed["c"]["r"], {"inputs.amount": 70}) is True


# -- US-21 ------------------------------------------------------------------------------------
def _x_then_c(c_guard: str | None, c_join: str = "all_done") -> str:
    return (
        step("x", "${inputs.amount} > 3", action="stats.aggregate")
        + step("c", c_guard, ["x"], c_join, extra='    with: {n: "${x.regions}"}\n')
        + step("z", None, ["c"], "all_done")
    )


def test_a_reference_to_a_step_that_can_be_skipped_is_a_warning_with_the_condition() -> None:
    got = findings(_x_then_c(None))
    assert codes(got) == ["W-AVAIL-001"]
    message = got[0].message
    assert "${x.regions} may not exist" in message
    assert "'x' is not guaranteed to have succeeded" in message
    assert "(${inputs.amount} > 3) is false" in message  # names the condition that skips it
    assert "inputs.amount = 0" in message  # and a value that shows it


def test_no_warning_when_the_guard_of_the_reader_implies_the_guard_of_the_producer() -> None:
    assert findings(_x_then_c("${inputs.amount} > 5")) == []
    assert codes(findings(_x_then_c("${inputs.amount} > 2"))) == ["W-AVAIL-001"]  # 3 gets in
    got = findings(_x_then_c("${inputs.amount} > 2"))
    assert "inputs.amount = 3" in got[0].message


def test_a_dependency_that_must_succeed_is_always_available() -> None:
    assert findings(_x_then_c(None, "all_success")) == []


def test_a_step_that_always_runs_is_available_behind_all_done() -> None:
    steps = (
        step("x", action="stats.aggregate")
        + step("c", None, ["x"], "all_done", extra='    with: {n: "${x.regions}"}\n')
        + step("z", None, ["c"])
    )
    assert findings(steps) == []


def test_a_step_that_may_fail_is_not_guaranteed_behind_all_done() -> None:
    steps = (
        step("x", action="stats.aggregate", extra="    on_failure: continue\n")
        + step("c", None, ["x"], "all_done", extra='    with: {n: "${x.regions}"}\n')
        + step("z", None, ["c"])
    )
    got = findings(steps)
    assert codes(got) == ["W-AVAIL-001"]
    assert "on_failure: continue" in got[0].message


def test_complementary_branches_make_a_common_ancestor_available() -> None:
    """The case a plain intersection of sets cannot see (the Proposal, C2)."""
    steps = (
        step("r", action="stats.aggregate", extra="    on_failure: continue\n")
        + step("a", "${inputs.amount} > 50", ["r"])
        + step("b", "${inputs.amount} <= 50", ["r"])
        + step("c", None, ["a", "b"], "any_success", extra='    with: {n: "${r.regions}"}\n')
        + step("z", None, ["c"])
    )
    assert findings(steps) == []


def test_overlapping_branches_do_not() -> None:
    steps = (
        step("r", action="stats.aggregate", extra="    on_failure: continue\n")
        + step("a", "${inputs.amount} > 50", ["r"])
        + step("b", "${inputs.amount} > 20")  # does not pass r on
        + step("c", None, ["a", "b"], "any_success", extra='    with: {n: "${r.regions}"}\n')
        + step("z", None, ["c"])
    )
    got = only("W-AVAIL", findings(steps))
    assert codes(got) == ["W-AVAIL-001"]
    assert "inputs.amount = " in got[0].message


def test_a_reference_in_a_condition_has_no_assumption_but_exists_proves_it() -> None:
    unguarded = (
        step("x", "${inputs.amount} > 3", action="stats.aggregate")
        + step("c", "${x.regions} > 1", ["x"], "all_done")
        + step("z", None, ["c"], "all_done")
    )
    assert codes(findings(unguarded)) == ["W-AVAIL-001"]
    proven = unguarded.replace("${x.regions} > 1", "exists(${x.regions}) and ${x.regions} > 1")
    assert findings(proven) == []
    wrong_order = unguarded.replace("${x.regions} > 1", "${x.regions} > 1 and exists(${x.regions})")
    assert codes(findings(wrong_order)) == ["W-AVAIL-001"]


def test_ensures_sees_the_assumption_of_the_guard() -> None:
    steps = (
        step("x", "${inputs.amount} > 3", action="stats.aggregate")
        + step(
            "c",
            "${inputs.amount} > 5",
            ["x"],
            "all_done",
            extra='    ensures: "${x.regions} > 0"\n',
        )
        + step("z", None, ["c"], "all_done")
    )
    assert findings(steps) == []
    assert codes(findings(steps.replace("${inputs.amount} > 5", "${inputs.amount} > 1"))) == [
        "W-AVAIL-001"
    ]


def test_a_condition_the_solver_cannot_read_gives_a_note_and_no_claim() -> None:
    steps = _x_then_c("len('abc') > 2")
    got = findings(steps)
    assert codes(got) == ["I-AVAIL-003"]
    assert got[0].severity is Severity.INFO


def test_the_example_has_no_finding() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "examples"
    text = (root / "quarterly_consolidation.yaml").read_text(encoding="utf8")
    regions = tuple({"code": c, "name": c} for c in "ABCDEFGHI")
    plan = compile_workflow(text, Catalogue({"regions": regions})).plan
    assert plan is not None and analyse(plan, _real()) == []


def _real() -> Signatures:
    return default_registry().signatures()


def test_available_at_lists_what_can_be_read_without_a_warning() -> None:
    plan = compile_plan(_x_then_c("${inputs.amount} > 5"), "z")
    assert available_at(plan, SIGNATURES, "c") == {"x"}
    plan = compile_plan(_x_then_c(None), "z")
    assert available_at(plan, SIGNATURES, "c") == set()
    assert available_at(plan, SIGNATURES, "z") == set() or "x" not in available_at(
        plan, SIGNATURES, "z"
    )


# -- US-22: groups ----------------------------------------------------------------------------
GROUP = (
    "  load:\n    foreach: region in catalogue.regions\n    action: x.y\n"
    "  check:\n    foreach: region in catalogue.regions\n    action: x.y\n"
    '    needs: ["load[region]"]\n'
)


def only_avail(*args: object, **kw: object) -> list[Diagnostic]:
    return only("W-AVAIL", findings(*args, **kw))  # type: ignore[arg-type]


def group_reader(index: int, join: str) -> str:
    return (
        GROUP
        + "  z:\n    action: x.y\n"
        + '    needs: ["check[*]"]\n'
        + f"    join: {join}\n"
        + f'    when: "len(${{check[*].rows}}[{index}]) > 0"\n'
    )


def test_a_reference_that_needs_more_elements_than_the_quorum_guarantees() -> None:
    # two regions in the test catalogue: at_least 1 guarantees 1 element, index 1 needs 2
    got = only_avail(group_reader(1, "{at_least: 1}"))
    assert codes(got) == ["W-AVAIL-002"]
    assert "only 1" in got[0].message
    assert only_avail(group_reader(1, "{at_least: 2}")) == []
    assert only_avail(group_reader(1, "all_success")) == []
    assert codes(only_avail(group_reader(0, "all_done"))) == ["W-AVAIL-002"]
    assert codes(only_avail(group_reader(1, "any_success"))) == ["W-AVAIL-002"]


def test_the_largest_element_of_an_empty_group_is_an_error_waiting() -> None:
    steps = (
        GROUP
        + '  z:\n    action: x.y\n    needs: ["check[*]"]\n    join: all_done\n'
        + '    when: "max(${check[*].rows}) > 0"\n'
    )
    got = only_avail(steps)
    assert codes(got) == ["W-AVAIL-002"] and "1 element" in got[0].message


def test_the_guarantee_travels_through_a_step_in_between() -> None:
    steps = (
        GROUP
        + '  mid:\n    action: x.y\n    needs: ["check[*]"]\n    join: {at_least: 2}\n'
        + '  z:\n    action: x.y\n    needs: [mid]\n    when: "len(${check[*].rows}[1]) > 0"\n'
    )
    assert only_avail(steps) == []
    weaker = steps.replace("at_least: 2", "at_least: 1")
    assert codes(only_avail(weaker)) == ["W-AVAIL-002"]


# -- US-22: goals -----------------------------------------------------------------------------
def goals(kind: str, a: str, b: str) -> list[Diagnostic]:
    steps = step("a", a) + step("b", b)
    head = "workflow: demo\nversion: 1\ninputs:\n" + "  amount: {type: int, min: 0, max: 100}\n"
    text = head + "steps:\n" + steps + f"goals: {{{kind}: [a, b]}}\n"
    plan = compile_workflow(text, Catalogue({})).plan
    assert plan is not None
    return analyse(plan, SIGNATURES)


def test_goals_that_do_not_cover_every_case() -> None:
    got = goals("any_of", "${inputs.amount} > 60", "${inputs.amount} < 40")
    assert codes(got) == ["W-GOAL-001"]
    assert "no goal is reached" in got[0].message and "inputs.amount = " in got[0].message
    text = "workflow: demo\n"
    assert got[0].line > 1 and text  # at the goals


def test_goals_that_cover_everything_are_quiet() -> None:
    assert goals("any_of", "${inputs.amount} > 50", "${inputs.amount} <= 50") == []


def test_all_of_names_each_goal_that_may_be_skipped() -> None:
    got = goals("all_of", "${inputs.amount} > 50", "${inputs.amount} <= 100")
    assert codes(got) == ["W-GOAL-001"]
    assert "the goal 'a' may be skipped" in got[0].message
    assert len(goals("all_of", "${inputs.amount} > 50", "${inputs.amount} > 60")) == 2


# -- dead steps by inheritance, undo ----------------------------------------------------------
def test_a_step_behind_a_dead_step_is_dead_too() -> None:
    steps = (
        step("a", "${inputs.amount} > 100")  # amount is at most 100
        + step("b", None, ["a"])
        + step("z", None, ["b"])
    )
    got = only("W-DEAD", findings(steps))
    # 'a' is dead by its own guard (the guard analysis says so); here only the inheritance
    assert [d.message for d in got] == [
        "'b' can never run: 'a' can never run",
        "'z' can never run: 'b' can never run",
    ]
    assert any("'b' can never run" in d.message for d in got)


def test_all_done_does_not_die_with_its_dependency() -> None:
    steps = step("a", "${inputs.amount} > 100") + step("b", None, ["a"], "all_done")
    got = findings(steps, "b")
    assert [d.message for d in got if "'b'" in d.message] == []


def test_a_quorum_that_cannot_be_reached_is_dead() -> None:
    steps = (
        step("a", "${inputs.amount} > 100")
        + step("b", "${inputs.amount} > 100")
        + step("c")
        + step("z", None, ["a", "b", "c"], "{at_least: 2}")
    )
    got = [d for d in only("W-DEAD", findings(steps)) if d.message.startswith("'z'")]
    assert len(got) == 1 and "can never run" in got[0].message


def test_an_effect_without_an_undo_is_named_when_the_workflow_undoes_runs() -> None:
    effect = "  w:\n    action: db.write\n    with: {rows: []}\n"
    plain = effect + step("z", None, ["w"])
    assert [d for d in findings(plain, signatures=_real()) if d.code == "W-COMP-001"] == []
    undoing = (
        plain + "  k:\n    action: xlsx.read\n    with: {path: 'a'}\n    on_failure: compensate\n"
    )
    got = [d for d in findings(undoing, signatures=_real()) if d.code == "W-COMP-001"]
    assert len(got) == 1 and "'w'" in got[0].message
    fixed = undoing.replace("action: db.write", "action: db.write\n    compensate: db.delete_batch")
    assert [d for d in findings(fixed, signatures=_real()) if d.code == "W-COMP-001"] == []
