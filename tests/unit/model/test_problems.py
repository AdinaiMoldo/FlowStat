"""US-56: a problem is opened for each error of a form; a corrected form moves it to revalidating
and then to resolved or reopened, and a problem that was resolved comes back when its error does."""

from __future__ import annotations

import pytest

from flowstat.model import problems as pr
from flowstat.model.diagnostics import Severity
from flowstat.model.problems import Change, Problem, ProblemError, key_of, reconcile
from flowstat.model.rules import Finding


def error(
    code: str = "VAL-RANGE-001",
    indicator: str = "I04",
    cell: str = "C5",
    severity: Severity = Severity.ERROR,
) -> Finding:
    return Finding(
        code, severity, f"{cell}: x", "BAT", "BAT/2025Q1.xlsx", "S", 5, cell, indicator, "-1", "0+"
    )


def problem(f: Finding, status: str = pr.OPEN, pid: str = "p1") -> Problem:
    return Problem(
        pid, "BAT", "2025Q1", key_of(f), f.code, f.indicator, f.cell, f.message, f.actual,
        f.expected, status, "olga", 1, 1, "2026-11-08T10:00:00Z", "2026-11-08T10:00:00Z",
    )  # fmt: skip


# -- the identity of a problem -----------------------------------------------------------------
def test_a_problem_is_the_same_for_the_same_code_and_indicator_wherever_the_cell_is() -> None:
    assert key_of(error(cell="C5")) == key_of(error(cell="C9"))
    assert key_of(error(indicator="I03")) != key_of(error(indicator="I04"))
    assert key_of(error("VAL-STR-004", "", "A3")) == "VAL-STR-004|A3"  # no indicator: the cell


# -- a first form --------------------------------------------------------------------------------
def test_each_error_of_a_first_form_opens_a_problem() -> None:
    a, b = error(indicator="I04"), error("VAL-REL-001", "I02", "C3")
    changes = reconcile([], [a, b])
    assert changes == (Change(None, a, (pr.OPEN,)), Change(None, b, (pr.OPEN,)))


def test_warnings_and_notes_open_nothing() -> None:
    notes = [error(severity=Severity.WARNING), error("VAL-COMP-003", severity=Severity.INFO)]
    assert reconcile([], notes) == ()


def test_the_same_mistake_twice_in_one_form_is_one_problem() -> None:
    twice = [error("VAL-STR-004", "", "A3"), error("VAL-STR-004", "", "A3")]
    assert len(reconcile([], twice)) == 1


# -- a corrected form ---------------------------------------------------------------------------
def test_an_error_that_is_gone_goes_through_revalidating_to_resolved() -> None:
    gone = error()
    (change,) = reconcile([problem(gone)], [])
    assert change.problem == problem(gone)
    assert change.path == (pr.REVALIDATING, pr.RESOLVED) and change.finding is None


def test_an_error_that_is_still_there_goes_through_revalidating_to_reopened() -> None:
    still = error(cell="C6")  # moved to another row, still the same mistake
    (change,) = reconcile([problem(error())], [still])
    assert change.path == (pr.REVALIDATING, pr.REOPENED) and change.finding == still


def test_a_reopened_problem_that_is_corrected_the_second_time_is_resolved() -> None:
    (change,) = reconcile([problem(error(), pr.REOPENED)], [])
    assert change.path == (pr.REVALIDATING, pr.RESOLVED)


def test_new_errors_open_problems_beside_the_ones_that_were_there() -> None:
    old, new = error(), error("VAL-REL-001", "I02", "C3")
    changes = reconcile([problem(old)], [old, new])
    assert [(c.problem is None, c.path) for c in changes] == [
        (False, (pr.REVALIDATING, pr.REOPENED)),
        (True, (pr.OPEN,)),
    ]


# -- a resolved problem ---------------------------------------------------------------------------
def test_a_resolved_problem_is_left_alone_while_its_error_stays_away() -> None:
    assert reconcile([problem(error(), pr.RESOLVED)], []) == ()
    other = error("VAL-REL-001", "I02", "C3")
    changes = reconcile([problem(error(), pr.RESOLVED)], [other])
    assert [c.path for c in changes] == [(pr.OPEN,)]  # only the new one


def test_a_resolved_problem_comes_back_when_its_error_does() -> None:
    back = error()
    (change,) = reconcile([problem(back, pr.RESOLVED)], [back])
    assert change.path == (pr.REOPENED,) and change.problem is not None


# -- what a person may do --------------------------------------------------------------------------
def test_a_problem_can_be_assigned_until_it_is_resolved() -> None:
    for status in (pr.OPEN, pr.REOPENED, pr.REVALIDATING):
        pr.check_assignable(problem(error(), status))
    with pytest.raises(ProblemError) as info:
        pr.check_assignable(problem(error(), pr.RESOLVED))
    assert info.value.code == "E-PROB-003"


@pytest.mark.parametrize("text", ["", "   ", "x" * 2001])
def test_a_comment_has_text_and_not_too_much(text: str) -> None:
    with pytest.raises(ProblemError) as info:
        pr.checked_comment(text)
    assert info.value.code == "E-PROB-001"


def test_a_comment_is_trimmed() -> None:
    assert pr.checked_comment("  fixed the deaths  ") == "fixed the deaths"


def test_the_statuses_that_still_need_work_are_the_active_ones() -> None:
    assert {pr.OPEN, pr.REOPENED, pr.REVALIDATING} == pr.ACTIVE
    assert pr.RESOLVED not in pr.ACTIVE
