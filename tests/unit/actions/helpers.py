"""Helpers of the action tests: synthetic regional forms and a ready context."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from openpyxl import Workbook

from flowstat.actions.base import ActionContext

Row = Sequence[object]


def write_form(path: Path, rows: Sequence[Row], header: Row = ("code", "name", "value")) -> Path:
    """A synthetic regional form: one sheet, a header row and one row per indicator."""
    path.parent.mkdir(parents=True, exist_ok=True)
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(list(header))
    for row in rows:
        sheet.append(list(row))
    book.save(path)
    return path


GOOD_ROWS: list[Row] = [
    ("I01", "Population", 100),
    ("I02", "Households", 40.5),
    ("I03", "Births", 7),
]


def context(tmp_path: Path, run_id: str = "run-1", step: str = "s") -> ActionContext:
    return ActionContext(run_id, step, tmp_path / "data", str(tmp_path / "target.db"))


# the five indicators of the sample forms, which the rules of the template ask for (US-54)
FIVE_ROWS: list[Row] = [
    ("I01", "Population", 400),
    ("I02", "Households", 111.5),
    ("I03", "Births", 6.5),
    ("I04", "Deaths", 2.5),
    ("I05", "Marriages", 2.75),
]

REGION_CODES = ["BIS", "OSC", "BAT", "JAL", "ISK", "NAR", "OSH", "TAL", "CHU"]


def write_all_forms(
    data_dir: Path,
    quarter: str,
    missing: Sequence[str] = (),
    rows: Sequence[Row] = GOOD_ROWS,
) -> None:
    """One synthetic form per region; the regions in ``missing`` never sent theirs."""
    for code in REGION_CODES:
        if code not in missing:
            write_form(data_dir / code / f"{quarter}.xlsx", rows)
