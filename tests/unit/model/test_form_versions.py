"""US-55: a form that is sent twice has two versions; what the second fixed is told from the
findings of the two, which are kept."""

from __future__ import annotations

from flowstat.model.diagnostics import Severity
from flowstat.model.form_versions import (
    Comparison,
    FormVersion,
    compare,
    dump_findings,
    load_findings,
)
from flowstat.model.rules import Finding


def finding(
    code: str = "VAL-RANGE-001",
    indicator: str = "I04",
    cell: str = "C5",
    severity: Severity = Severity.ERROR,
    actual: str = "-1",
) -> Finding:
    return Finding(
        code, severity, f"{cell}: x", "BAT", "BAT/2025Q1.xlsx", "Sheet", 5, cell, indicator,
        actual, "0 or more",
    )  # fmt: skip


def test_what_is_in_the_first_and_not_in_the_second_is_fixed() -> None:
    gone, kept = finding(indicator="I04"), finding("VAL-REL-001", "I02", "C3")
    result = compare([gone, kept], [kept])
    assert result == Comparison(fixed=(gone,), remaining=(kept,), introduced=())


def test_what_is_only_in_the_second_is_new() -> None:
    new = finding("VAL-COMP-001", "I05", "")
    assert compare([], [new]) == Comparison((), (), (new,))


def test_the_same_mistake_in_another_cell_is_still_the_same_mistake() -> None:
    before, after = finding(cell="C5", actual="-1"), finding(cell="C6", actual="-7")
    result = compare([before], [after])
    assert result.fixed == () and result.introduced == () and result.remaining == (after,)


def test_the_same_code_for_another_indicator_is_another_mistake() -> None:
    a, b = finding(indicator="I03"), finding(indicator="I04")
    result = compare([a], [b])
    assert result.fixed == (a,) and result.introduced == (b,)


def test_a_mistake_that_was_twice_and_is_once_has_one_fixed() -> None:
    twice = [finding("VAL-STR-004", "", "A3"), finding("VAL-STR-004", "", "A4")]
    result = compare(twice, twice[:1])
    assert len(result.fixed) == 1 and len(result.remaining) == 1 and result.introduced == ()


def test_identical_findings_leave_nothing_fixed_and_nothing_new() -> None:
    same = [finding(), finding("VAL-REL-001", "I02", "C3")]
    result = compare(same, same)
    assert result.fixed == () and result.introduced == () and len(result.remaining) == 2


def test_a_form_without_findings_on_both_sides_compares_to_nothing() -> None:
    assert compare([], []) == Comparison((), (), ())


def test_findings_are_stored_as_text_and_come_back_the_same() -> None:
    kept = (finding(), finding("VAL-STR-005", "I03", "B4", Severity.WARNING, "Birth count"))
    assert load_findings(dump_findings(kept)) == kept
    assert load_findings(dump_findings(())) == ()


def test_a_version_counts_its_errors_and_its_warnings() -> None:
    found = (
        finding(),
        finding(severity=Severity.WARNING),
        finding("VAL-COMP-003", severity=Severity.INFO),
    )
    version = FormVersion(
        "BAT", "2025Q1", 2, "ab" * 32, 1234, 5, "olga", "2026-11-08T10:00:00Z", "basic", found
    )
    assert (version.errors, version.warnings) == (1, 1)
    assert version.removed_at is None
