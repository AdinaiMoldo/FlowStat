"""xlsx.read: a regional form (an Excel file) becomes rows."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from typing import IO

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from flowstat.actions.base import ActionContext, ActionError, Args

HEADER = ("code", "name", "value")


@dataclass(frozen=True)
class Sheet:
    """The first sheet of a form as the content rules need it (US-54)."""

    title: str
    sheets: int  # how many sheets the file has; only the first is read
    extra_columns: bool  # there are cells right of the third column
    rows: list[dict[str, object]]
    positions: list[int]  # the row of the sheet that each row came from (the header is row 1)


def read_sheet(stream: IO[bytes], label: str) -> Sheet:
    """The first sheet of a form that is already open: header ``code, name, value``, then one row
    per record. ``label`` names the form in a message (a path, or "the file"). The stream is the
    caller's, so that it is closed by the caller whatever this does (a file that is not a form can
    leave a handle open in the reader, and Windows will not replace or remove such a file)."""
    try:
        book = load_workbook(stream, read_only=True, data_only=True)
    except (zipfile.BadZipFile, InvalidFileException, KeyError):
        raise ActionError("E-ACT-003", f"{label} is not a valid xlsx file") from None
    try:
        sheet = book.worksheets[0]
        lines = sheet.iter_rows(values_only=True)
        first = next(lines, ())
        header = tuple(str(c).strip().lower() if c is not None else "" for c in first[:3])
        if header != HEADER:
            raise ActionError(
                "E-ACT-003", f"{label}: the first row must be {', '.join(HEADER)}, not {header}"
            )
        rows: list[dict[str, object]] = []
        positions: list[int] = []
        wide = any(c is not None for c in first[3:])
        for number, line in enumerate(lines, start=2):
            cells = tuple(line[:3]) + (None,) * (3 - len(line[:3]))
            wide = wide or any(c is not None for c in line[3:])
            if all(c is None for c in cells):
                continue
            rows.append(dict(zip(HEADER, cells, strict=True)))
            positions.append(number)
        return Sheet(sheet.title, len(book.worksheets), wide, rows, positions)
    finally:
        book.close()


def rows_of(stream: IO[bytes], label: str) -> list[dict[str, object]]:
    """The rows of a form that is already open (see ``read_sheet``)."""
    return read_sheet(stream, label).rows


def read_form(args: Args, ctx: ActionContext) -> dict[str, object]:
    """Read ``path`` (relative to the data directory): header ``code, name, value``, then rows."""
    path = str(args["path"])
    base = ctx.data_dir.resolve()
    full = (base / path).resolve()
    if not full.is_relative_to(base):
        raise ActionError("E-ACT-007", f"path {path} is outside the data directory")
    try:
        with open(full, "rb") as stream:
            rows = rows_of(stream, path)
    except FileNotFoundError:
        raise ActionError("E-ACT-002", f"cannot read {path}: file not found") from None
    return {"rows": rows, "count": len(rows)}
