"""Make synthetic regional forms to send through the page (US-50).

Usage:
    python scripts/make_forms.py [--out sample-forms]

Writes three sets of Excel files, one file for each region, named by the code of the region
(``BIS.xlsx``), so that they can be chosen together on the page "Forms":

    good/            nine forms that pass every check: the run reaches the approval
    six-regions/     six forms: three regions sent nothing, which is fewer than the seven that the
                     consolidation needs (the goal is not reached)
    with-a-mistake/  nine forms, one of which has a negative value: the run stops at its check

Only synthetic data: the numbers are invented and the same at every call.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from openpyxl import Workbook

REGIONS = ("BIS", "OSC", "BAT", "JAL", "ISK", "NAR", "OSH", "TAL", "CHU")
INDICATORS = (
    ("I01", "Population"),
    ("I02", "Households"),
    ("I03", "Births"),
    ("I04", "Deaths"),
    ("I05", "Marriages"),
)
MISTAKE_IN = "BAT"
MISSING = ("NAR", "TAL", "CHU")  # the regions of ``six-regions`` that sent nothing


def rows_of(region: str, mistake: bool = False) -> list[tuple[str, str, float]]:
    """The invented figures of a region: they depend on its place in the list and nothing else."""
    n = REGIONS.index(region) + 1
    values = (300 + 97 * n, 90 + 21 * n, 5.0 + 1.5 * n, 2.0 + 0.5 * n, 2.5 + 0.25 * n)
    rows = [(code, name, value) for (code, name), value in zip(INDICATORS, values, strict=True)]
    if mistake:
        rows[3] = (rows[3][0], rows[3][1], -abs(rows[3][2]))  # deaths below zero
    return rows


def write_form(path: Path, rows: list[tuple[str, str, float]]) -> None:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["code", "name", "value"])
    for row in rows:
        sheet.append(list(row))
    book.save(path)


def write_sets(out: Path) -> dict[str, list[Path]]:
    """Make the three sets under ``out``; the files of each, by the name of the set."""
    sets: dict[str, list[Path]] = {"good": [], "six-regions": [], "with-a-mistake": []}
    for name, regions, bad in (
        ("good", REGIONS, ""),
        ("six-regions", [r for r in REGIONS if r not in MISSING], ""),
        ("with-a-mistake", REGIONS, MISTAKE_IN),
    ):
        folder = out / name
        folder.mkdir(parents=True, exist_ok=True)
        for region in regions:
            path = folder / f"{region}.xlsx"
            write_form(path, rows_of(region, mistake=region == bad))
            sets[name].append(path)
    return sets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("sample-forms"))
    args = parser.parse_args(argv)
    for name, files in write_sets(args.out).items():
        print(f"{args.out / name}: {len(files)} forms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
