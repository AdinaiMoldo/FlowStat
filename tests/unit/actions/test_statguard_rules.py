"""US-54: the reader tells where each row came from, and ``statguard.validate`` takes a rule set."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from openpyxl import Workbook

from flowstat.actions.base import ActionError
from flowstat.actions.statguard import validate, violations
from flowstat.actions.xlsx import read_sheet, rows_of
from tests.unit.actions.helpers import context, write_form

FIVE = [
    ("I01", "Population", 400),
    ("I02", "Households", 111.5),
    ("I03", "Births", 6.5),
    ("I04", "Deaths", 2.5),
    ("I05", "Marriages", 2.75),
]


def rows(*lines: tuple[object, object, object]) -> list[dict[str, object]]:
    return [dict(zip(("code", "name", "value"), line, strict=True)) for line in lines]


def book_bytes(book: Workbook) -> io.BytesIO:
    stream = io.BytesIO()
    book.save(stream)
    stream.seek(0)
    return stream


# -- the reader --------------------------------------------------------------------------------
def test_the_reader_keeps_the_row_of_the_sheet_each_row_came_from(tmp_path: Path) -> None:
    path = write_form(
        tmp_path / "f.xlsx", [FIVE[0], (None, None, None), FIVE[1], (None,) * 3, FIVE[2]]
    )
    with open(path, "rb") as stream:
        sheet = read_sheet(stream, "f")
    assert [r["code"] for r in sheet.rows] == ["I01", "I02", "I03"]
    assert sheet.positions == [2, 4, 6]  # the empty lines are skipped but counted
    assert sheet.sheets == 1 and sheet.extra_columns is False and sheet.title


def test_the_reader_sees_other_sheets_and_cells_right_of_the_value() -> None:
    book = Workbook()
    first = book.active
    assert first is not None
    first.title = "Form"
    first.append(["code", "name", "value", "comment"])
    first.append(["I01", "Population", 5, "a note"])
    book.create_sheet("Other")
    sheet = read_sheet(book_bytes(book), "the file")
    assert (sheet.title, sheet.sheets, sheet.extra_columns) == ("Form", 2, True)
    assert rows_of(book_bytes(book), "the file") == sheet.rows  # the old function is the same


# -- the step ------------------------------------------------------------------------------------
def test_without_rules_the_basic_ones_apply_as_before() -> None:
    data = rows(("I01", "Population", 100))
    assert validate({"rows": data}, context(Path("."))) == {"rows": data, "checked": 1}
    assert validate({"rows": data, "rules": "basic"}, context(Path(".")))["checked"] == 1


def test_a_rule_set_of_a_template_asks_for_every_indicator() -> None:
    args = {"rows": rows(("I01", "Population", 100)), "rules": "regional-indicators-v1"}
    with pytest.raises(ActionError) as info:
        validate(args, context(Path(".")))
    assert info.value.code == "E-ACT-005" and "I02 Households is missing" in info.value.message


def test_a_good_form_passes_the_template_and_warnings_do_not_stop_it() -> None:
    good = rows(*FIVE)
    args = {"rows": good, "rules": "regional-indicators-v1"}
    assert validate(args, context(Path(".")))["checked"] == 5
    odd = rows(*FIVE, ("X1", "Other", 1))  # a code the template does not know: a warning
    assert validate({**args, "rows": odd}, context(Path(".")))["checked"] == 6


def test_a_relation_that_fails_stops_the_step_and_says_where() -> None:
    bad = rows(*FIVE)
    bad[1]["value"] = 999
    with pytest.raises(ActionError) as info:
        validate({"rows": bad, "rules": "regional-indicators-v1"}, context(Path(".")))
    assert info.value.code == "E-ACT-005"
    assert "C3 (I02): 999 is more than 400 in I01" in info.value.message


def test_an_unknown_rule_set_is_an_error_of_the_argument() -> None:
    with pytest.raises(ActionError) as info:
        validate({"rows": rows(("I01", "P", 1)), "rules": "strict"}, context(Path(".")))
    assert info.value.code == "E-ACT-009" and "strict" in info.value.message


def test_violations_is_the_basic_rules_in_words() -> None:
    assert violations(rows(("I01", "P", -1))) == ["C2 (I01): value is negative"]
    assert violations(rows(*FIVE)) == []
