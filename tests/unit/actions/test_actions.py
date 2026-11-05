"""Tests of the step actions (B6): schema of the output, and the path in the error message."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from flowstat.actions.base import ActionError
from flowstat.actions.database import delete_batch, write_rows
from flowstat.actions.email import send_email
from flowstat.actions.statguard import validate
from flowstat.actions.stats import aggregate
from flowstat.actions.xlsx import read_form
from tests.unit.actions.helpers import GOOD_ROWS, context, write_form


def rows(*items: tuple[str, str, object]) -> list[dict[str, object]]:
    return [{"code": c, "name": n, "value": v} for c, n, v in items]


# -- xlsx.read ----------------------------------------------------------------------------------
def test_a_valid_form_gives_rows_and_a_count(tmp_path: Path) -> None:
    write_form(tmp_path / "data" / "BIS" / "2025Q1.xlsx", GOOD_ROWS)
    out = read_form({"path": "BIS/2025Q1.xlsx"}, context(tmp_path))
    assert out["count"] == 3
    assert out["rows"][0] == {"code": "I01", "name": "Population", "value": 100}  # type: ignore[index]


def test_empty_rows_of_a_form_are_ignored(tmp_path: Path) -> None:
    write_form(tmp_path / "data" / "f.xlsx", [*GOOD_ROWS, (None, None, None)])
    assert read_form({"path": "f.xlsx"}, context(tmp_path))["count"] == 3


def test_a_missing_file_fails_with_the_path_in_the_message(tmp_path: Path) -> None:
    with pytest.raises(ActionError) as info:
        read_form({"path": "BIS/2025Q1.xlsx"}, context(tmp_path))
    assert info.value.code == "E-ACT-002"
    assert "BIS/2025Q1.xlsx" in info.value.message


def test_a_form_with_wrong_headers_is_refused(tmp_path: Path) -> None:
    write_form(tmp_path / "data" / "f.xlsx", GOOD_ROWS, header=("code", "title", "amount"))
    with pytest.raises(ActionError) as info:
        read_form({"path": "f.xlsx"}, context(tmp_path))
    assert info.value.code == "E-ACT-003" and "f.xlsx" in info.value.message


def test_a_file_that_is_not_xlsx_is_refused(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "f.xlsx").write_text("not a workbook", encoding="utf8")
    with pytest.raises(ActionError) as info:
        read_form({"path": "f.xlsx"}, context(tmp_path))
    assert info.value.code == "E-ACT-003" and "f.xlsx" in info.value.message


def test_a_path_outside_the_data_directory_is_refused(tmp_path: Path) -> None:
    write_form(tmp_path / "secret.xlsx", GOOD_ROWS)
    with pytest.raises(ActionError) as info:
        read_form({"path": "../secret.xlsx"}, context(tmp_path))
    assert info.value.code == "E-ACT-007"


# -- statguard.validate -------------------------------------------------------------------------
def test_valid_rows_pass_unchanged() -> None:
    data = rows(("I01", "Population", 100), ("I02", "Households", 40.5))
    assert validate({"rows": data}, context(Path("."))) == {"rows": data, "checked": 2}


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        (rows(("I01", "P", "many")), "value is not a number"),
        (rows(("I01", "P", -1)), "value is negative"),
        (rows(("I01", "P", None)), "value is not a number"),
        (rows(("", "P", 1)), "code is empty"),
        (rows(("I01", "P", 1), ("I01", "Q", 2)), "duplicate code I01"),
        ([], "the form has no rows"),
    ],
)
def test_rule_violations_fail_the_step_and_name_the_row(
    bad: list[dict[str, object]], reason: str
) -> None:
    with pytest.raises(ActionError) as info:
        validate({"rows": bad}, context(Path(".")))
    assert info.value.code == "E-ACT-005" and reason in info.value.message


def test_a_boolean_is_not_a_number() -> None:
    with pytest.raises(ActionError):
        validate({"rows": rows(("I01", "P", True))}, context(Path(".")))


# -- stats.aggregate ----------------------------------------------------------------------------
def test_regions_are_summed_by_code_in_code_order() -> None:
    parts = [
        rows(("I02", "Households", 1.5), ("I01", "Population", 10)),
        rows(("I01", "Population", 5), ("I02", "Households", 2.5)),
    ]
    out = aggregate({"parts": parts}, context(Path(".")))
    assert out == {
        "rows": rows(("I01", "Population", 15.0), ("I02", "Households", 4.0)),
        "regions": 2,
    }


def test_aggregate_of_no_regions_is_empty() -> None:
    assert aggregate({"parts": []}, context(Path("."))) == {"rows": [], "regions": 0}


# -- db.write, db.delete_batch, email.send ------------------------------------------------------
def stored(tmp_path: Path) -> list[tuple[str, str, float]]:
    db = sqlite3.connect(tmp_path / "target.db")
    try:
        return db.execute(
            "SELECT batch, code, value FROM published ORDER BY batch, code"
        ).fetchall()
    finally:
        db.close()


def test_write_stores_the_rows_under_the_batch_of_the_run(tmp_path: Path) -> None:
    out = write_rows({"rows": rows(("I01", "P", 15.0), ("I02", "H", 4.0))}, context(tmp_path, "r7"))
    assert out == {"written": 2, "batch": "r7"}
    assert stored(tmp_path) == [("r7", "I01", 15.0), ("r7", "I02", 4.0)]


def test_writing_twice_does_not_duplicate(tmp_path: Path) -> None:
    ctx = context(tmp_path, "r7")
    write_rows({"rows": rows(("I01", "P", 15.0))}, ctx)
    write_rows({"rows": rows(("I01", "P", 16.0))}, ctx)
    assert stored(tmp_path) == [("r7", "I01", 16.0)]


def test_delete_batch_removes_only_the_batch_of_the_run(tmp_path: Path) -> None:
    write_rows({"rows": rows(("I01", "P", 1.0))}, context(tmp_path, "r1"))
    write_rows({"rows": rows(("I01", "P", 2.0))}, context(tmp_path, "r2"))
    assert delete_batch({}, context(tmp_path, "r1")) == {"deleted": 1}
    assert stored(tmp_path) == [("r2", "I01", 2.0)]


def test_write_without_a_target_database_fails(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    ctx = type(ctx)(ctx.run_id, ctx.step, ctx.data_dir, None)
    with pytest.raises(ActionError) as info:
        write_rows({"rows": []}, ctx)
    assert info.value.code == "E-ACT-006"


def test_a_row_the_table_cannot_hold_fails_the_step(tmp_path: Path) -> None:
    with pytest.raises(ActionError) as info:
        write_rows({"rows": [{"code": "I01"}]}, context(tmp_path))
    assert info.value.code == "E-ACT-006"


def test_email_goes_to_the_outbox_and_sends_nothing(tmp_path: Path) -> None:
    out = send_email({}, context(tmp_path, "r1", "return_to_regions"))
    assert out == {"queued": 1}
    db = sqlite3.connect(tmp_path / "target.db")
    try:
        assert db.execute("SELECT run_id, step FROM outbox").fetchall() == [
            ("r1", "return_to_regions")
        ]
    finally:
        db.close()
