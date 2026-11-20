"""The content rules of a regional form (US-54): exact arithmetic, ranges, relations between
indicators, the states of a cell kept apart, and a diagnostic that says where and why."""

from __future__ import annotations

from decimal import Decimal

import pytest

from flowstat.model import rules
from flowstat.model.diagnostics import Severity
from flowstat.model.rules import (
    BASIC,
    REGIONAL_V1,
    Finding,
    Indicator,
    RuleSet,
    State,
    SumOf,
    check,
    known,
    named,
    read_value,
)

Row = dict[str, object]


def row(code: object, name: object, value: object) -> Row:
    return {"code": code, "name": name, "value": value}


def good() -> list[Row]:
    return [
        row("I01", "Population", 400),
        row("I02", "Households", 111.5),
        row("I03", "Births", 6.5),
        row("I04", "Deaths", 2.5),
        row("I05", "Marriages", 2.75),
    ]


def codes(found: list[Finding]) -> list[str]:
    return [f.code for f in found]


# -- the states of a cell ---------------------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "state"),
    [
        (None, State.EMPTY),
        ("", State.EMPTY),
        ("   ", State.EMPTY),
        ("n/a", State.NOT_APPLICABLE),
        (" N/A ", State.NOT_APPLICABLE),
        ("many", State.INVALID),
        ("12", State.INVALID),
        (True, State.INVALID),
        (float("nan"), State.INVALID),
        (float("inf"), State.INVALID),
        (0, State.ZERO),
        (0.0, State.ZERO),
        (-3, State.VALUE),
        (40.5, State.VALUE),
    ],
)
def test_a_cell_is_in_exactly_one_state(value: object, state: State) -> None:
    assert read_value(value)[0] is state


def test_a_float_is_read_as_the_number_that_was_typed() -> None:
    assert read_value(0.1)[1] == Decimal("0.1")  # not 0.1000000000000000055...
    assert read_value(7)[1] == Decimal(7)
    assert read_value("many")[1] is None


# -- the basic rules: what every form must keep, whatever its indicators ----------------------
def test_a_good_form_has_no_findings_under_either_set() -> None:
    assert check(good(), BASIC) == []
    assert check(good(), REGIONAL_V1) == []


def test_basic_rules_are_the_old_ones_with_codes() -> None:
    found = check(
        [
            row("I01", "A", 5),
            row("I01", "B", -2),
            row("I03", "C", "many"),
            row("", "D", 1),
            row("I05", "E", None),
        ]
    )
    assert codes(found) == [
        "VAL-STR-004",  # duplicate code I01 (row 3)
        "VAL-RANGE-001",  # negative (row 3)
        "VAL-TYPE-001",  # text (row 4)
        "VAL-STR-003",  # code is empty (row 5)
        "VAL-COMP-002",  # the cell is empty (row 6)
    ]
    assert all(f.severity is Severity.ERROR for f in found)
    texts = [f.message for f in found]
    assert "duplicate code I01" in texts[0] and "value is negative" in texts[1]
    assert "value is not a number" in texts[2] and "code is empty" in texts[3]
    assert "value is not a number" in texts[4]  # an empty cell says the old words too


def test_no_rows_is_one_error_and_nothing_else() -> None:
    found = check([], REGIONAL_V1)
    assert codes(found) == ["VAL-STR-001"] and "the form has no rows" in found[0].message


def test_a_row_that_is_not_a_record_is_told() -> None:
    found = check([good()[0], "text"])
    assert codes(found) == ["VAL-STR-002"] and found[0].row == 3


def test_a_boolean_and_a_text_number_are_not_numbers() -> None:
    assert codes(check([row("I01", "P", True), row("I02", "H", "12")])) == ["VAL-TYPE-001"] * 2


def test_basic_rules_do_not_ask_for_indicators() -> None:
    assert check([row("I01", "Population", 1)], BASIC) == []
    assert "VAL-COMP-001" in codes(check([row("I01", "Population", 1)], REGIONAL_V1))


# -- where and why: every diagnostic is complete ----------------------------------------------
def test_a_finding_names_region_file_sheet_cell_actual_and_expected() -> None:
    rows = good()
    rows[3] = row("I04", "Deaths", -2.5)
    (found,) = check(rows, REGIONAL_V1, region="BAT", file="BAT/2025Q1.xlsx", sheet="Form")
    assert found.code == "VAL-RANGE-001" and found.severity is Severity.ERROR
    assert (found.region, found.file, found.sheet) == ("BAT", "BAT/2025Q1.xlsx", "Form")
    assert (found.row, found.cell, found.indicator) == (5, "C5", "I04")
    assert (found.actual, found.expected) == ("-2.5", "0 or more")
    assert found.message.startswith("C5 (I04)") and "value is negative" in found.message


def test_the_rows_of_a_sheet_are_numbered_from_the_second_unless_told_otherwise() -> None:
    rows = [row("I01", "P", -1), row("I02", "H", -1)]
    assert [f.cell for f in check(rows)] == ["C2", "C3"]
    # empty lines were skipped when the rows were read: the positions say where the rows were
    assert [f.cell for f in check(rows, positions=[2, 7])] == ["C2", "C7"]


# -- the template: completeness, ranges and relations -----------------------------------------
def test_an_indicator_that_is_not_in_the_form_is_missing_not_zero() -> None:
    found = check(good()[:3] + good()[4:], REGIONAL_V1)
    assert codes(found) == ["VAL-COMP-001"]
    only = found[0]
    assert only.indicator == "I04" and only.cell == "" and only.severity is Severity.ERROR
    assert "I04 Deaths is missing" in only.message and "I04" in only.expected


def test_empty_zero_missing_and_not_applicable_are_four_different_things() -> None:
    rows = good()
    rows[1] = row("I02", "Households", None)  # empty
    rows[2] = row("I03", "Births", 0)  # zero: a figure, reported
    rows[4] = row("I05", "Marriages", "n/a")  # not applicable
    found = check(rows[:4] + rows[4:], REGIONAL_V1)
    by_indicator = {f.indicator: f for f in found}
    assert (
        by_indicator["I02"].code == "VAL-COMP-002"
        and by_indicator["I02"].severity is Severity.ERROR
    )
    assert (
        by_indicator["I03"].code == "VAL-COMP-003" and by_indicator["I03"].severity is Severity.INFO
    )
    assert (
        by_indicator["I05"].code == "VAL-COMP-004"
        and by_indicator["I05"].severity is Severity.ERROR
    )
    missing = check(rows[:1] + rows[2:], REGIONAL_V1)
    assert "VAL-COMP-001" in codes(missing)


def test_not_applicable_is_allowed_only_where_the_template_says() -> None:
    rules_ = RuleSet(
        "t", indicators=(Indicator("A", "Alpha", may_be_na=True), Indicator("B", "Beta"))
    )
    found = check([row("A", "Alpha", "n/a"), row("B", "Beta", "n/a")], rules_)
    assert [(f.indicator, f.severity) for f in found] == [
        ("A", Severity.INFO),
        ("B", Severity.ERROR),
    ]


def test_the_first_indicator_must_be_more_than_zero() -> None:
    rows = good()
    rows[0] = row("I01", "Population", 0)
    found = check(rows, REGIONAL_V1)
    assert codes(found) == ["VAL-RANGE-002"]  # an error, and no note about a zero beside it
    assert found[0].expected == "more than 0" and found[0].actual == "0"


def test_a_range_has_an_upper_end_too() -> None:
    rules_ = RuleSet("t", indicators=(Indicator("P", "Share", low=Decimal(0), high=Decimal(100)),))
    assert check([row("P", "Share", 100)], rules_) == []
    found = check([row("P", "Share", 100.5)], rules_)
    assert codes(found) == ["VAL-RANGE-002"] and found[0].expected == "at most 100"


def test_a_part_cannot_be_more_than_the_whole() -> None:
    rows = good()
    rows[1] = row("I02", "Households", 400.5)  # more households than people
    found = check(rows, REGIONAL_V1)
    assert codes(found) == ["VAL-REL-001"]
    assert found[0].indicator == "I02" and found[0].cell == "C3"
    assert found[0].actual == "400.5" and found[0].expected == "at most 400 (I01)"


def test_a_relation_is_not_judged_when_one_side_is_not_a_figure() -> None:
    rows = good()
    rows[0] = row("I01", "Population", "many")
    assert codes(check(rows, REGIONAL_V1)) == ["VAL-TYPE-001"]  # no cascade of VAL-REL-001


def test_the_name_in_the_form_is_compared_with_the_template_as_a_warning() -> None:
    rows = good()
    rows[2] = row("I03", "Birth count", 6.5)
    (found,) = check(rows, REGIONAL_V1)
    assert found.code == "VAL-STR-005" and found.severity is Severity.WARNING
    assert found.expected == "Births" and found.actual == "Birth count"
    rows[2] = row("I03", "BIRTHS", 6.5)
    assert check(rows, REGIONAL_V1) == []  # case does not matter


def test_a_code_the_template_does_not_know_is_a_warning() -> None:
    (found,) = check([*good(), row("X99", "Other", 1)], REGIONAL_V1)
    assert found.code == "VAL-STR-008" and found.severity is Severity.WARNING
    assert check([*good(), row("X99", "Other", 1)], BASIC) == []


# -- arithmetic is exact ----------------------------------------------------------------------
TOTALS = RuleSet(
    "totals",
    indicators=(
        Indicator("T", "Total"),
        Indicator("A", "Part A"),
        Indicator("B", "Part B"),
        Indicator("C", "Part C"),
    ),
    sums=(SumOf("T", ("A", "B", "C")),),
)


def totals(t: object, a: object, b: object, c: object) -> list[Row]:
    return [
        row("T", "Total", t),
        row("A", "Part A", a),
        row("B", "Part B", b),
        row("C", "Part C", c),
    ]


def test_a_total_that_is_the_sum_of_its_parts_passes_even_when_floats_would_not() -> None:
    assert 0.1 + 0.2 != 0.3  # the reason for Decimal
    assert check(totals(0.6, 0.1, 0.2, 0.3), TOTALS) == []
    assert check(totals(1.1, 0.1, 0.2, 0.8), TOTALS) == []


def test_a_total_that_is_off_by_a_hair_is_an_error_that_says_by_how_much() -> None:
    (found,) = check(totals(0.61, 0.1, 0.2, 0.3), TOTALS)
    assert found.code == "VAL-ARITH-001" and found.severity is Severity.ERROR
    assert (found.indicator, found.cell) == ("T", "C2")
    assert (found.actual, found.expected) == ("0.61", "0.6 (A + B + C)")
    assert "0.01" in found.message


def test_a_sum_is_not_judged_when_a_part_is_missing_or_not_a_figure() -> None:
    assert codes(check(totals(10, 4, "n/a", 6), TOTALS)) == ["VAL-COMP-004"]
    assert codes(check(totals(10, 4, None, 6), TOTALS)) == ["VAL-COMP-002"]


def test_big_and_tiny_numbers_add_exactly() -> None:
    assert check(totals(1e15 + 0.5, 1e15, 0.25, 0.25), TOTALS) == []
    assert (
        check(totals(1e300, 1e300, 0, 0), TOTALS)[0].code == "VAL-COMP-003"
    )  # zero parts: notes only


# -- structure of the file --------------------------------------------------------------------
def test_other_sheets_and_extra_columns_are_warned_about_not_refused() -> None:
    found = check(good(), BASIC, sheets=3, extra_columns=True)
    assert codes(found) == ["VAL-STR-006", "VAL-STR-007"]
    assert all(f.severity is Severity.WARNING for f in found)
    assert check(good(), BASIC, sheets=1, extra_columns=False) == []


# -- the sets, by name ------------------------------------------------------------------------
def test_the_sets_are_found_by_name() -> None:
    assert named("basic") is BASIC and named("regional-indicators-v1") is REGIONAL_V1
    assert known("basic") and known("regional-indicators-v1") and not known("strict")
    with pytest.raises(KeyError):
        named("strict")


def test_helpers_tell_what_blocks_a_form() -> None:
    rows = good()
    rows[2] = row("I03", "Birth count", -1)
    found = check(rows, REGIONAL_V1)
    assert {f.severity for f in found} == {Severity.ERROR, Severity.WARNING}
    assert [f.code for f in rules.blocking(found)] == ["VAL-RANGE-001"]


def test_the_module_keeps_to_the_standard_library() -> None:
    import ast
    from pathlib import Path

    tree = ast.parse(Path(rules.__file__).read_text(encoding="utf8"))
    top = {
        (n.module or "").split(".")[0] if isinstance(n, ast.ImportFrom) else a.name.split(".")[0]
        for n in ast.walk(tree)
        for a in (n.names if isinstance(n, ast.Import | ast.ImportFrom) else [])
    }
    assert top <= {"__future__", "collections", "dataclasses", "decimal", "enum", "flowstat"}


# -- the demographic template with a total (US-63) --------------------------------------------
def demographic(**changes: object) -> list[dict[str, object]]:
    values: dict[str, object] = {
        "D01": 1000, "D02": 400, "D03": 600, "D04": 250, "D05": 21.5, "D06": 7, "D07": 8,
        "D08": 2,
    }  # fmt: skip
    values.update(changes)
    names = {i.code: i.name for i in named("regional-demography-v2").indicators}
    return [row(code, names[code], value) for code, value in values.items() if value != "absent"]


def test_the_demographic_template_passes_a_form_whose_total_is_the_sum_of_its_parts() -> None:
    rules = named("regional-demography-v2")
    assert known("regional-demography-v2") and rules.closed
    assert check(demographic(), rules) == []


def test_a_total_that_is_not_the_sum_of_urban_and_rural_is_an_arithmetic_error() -> None:
    found = check(demographic(D02=410), named("regional-demography-v2"))
    assert codes(found) == ["VAL-ARITH-001"]
    assert found[0].indicator == "D01" and found[0].actual == "1000"
    assert "1010" in found[0].expected and "differs by 10" in found[0].message


def test_a_missing_row_a_text_for_a_number_and_too_many_divorces_are_told_apart() -> None:
    rules = named("regional-demography-v2")
    assert codes(check(demographic(D06="absent"), rules)) == ["VAL-COMP-001"]
    assert codes(check(demographic(D05="about twenty"), rules)) == ["VAL-TYPE-001"]
    assert codes(check(demographic(D04=1200), rules)) == ["VAL-REL-001"]  # more homes than people
    assert codes(check(demographic(D02=-400, D03=1400), rules)) == ["VAL-RANGE-001"]
