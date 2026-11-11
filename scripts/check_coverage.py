"""Check that every package of C1 to C6 is covered by at least 90% (NFR-09).

Usage: python scripts/check_coverage.py [--json coverage.json] [--min 90]

The gate of ``pytest --cov`` is one number for the whole of ``flowstat``, and a package that is
badly covered can hide behind a package that is covered well. This script reads the report that
``python -m coverage json`` writes after the tests and judges each package of C1 to C6 by itself,
with lines and branches counted together, as the measurement of the audit of M9 did. It prints a
table and exits with 1 if a package is below the limit or has no measured file at all.

It uses the standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path

# The packages of C1 to C6 (docs/architecture.md; tests/architecture/rules.py keeps the same list).
PACKAGES = (
    "model",
    "dsl",
    "solver",
    "compiler",
    "analysis",
    "engine",
    "recovery",
    "migration",
)
DEFAULT_LIMIT = 90.0


def package_of(path: str) -> str | None:
    """The package of ``src/flowstat/<package>/<file>.py``, or None for any other file."""
    parts = path.replace("\\", "/").split("/")
    for i, part in enumerate(parts[:-2]):
        if part == "flowstat" and (i == 0 or parts[i - 1] == "src"):
            return parts[i + 1]
    return None


def totals(report: Mapping[str, object]) -> dict[str, tuple[int, int]]:
    """For each package: the parts covered and the parts there are (statements and branches)."""
    files = report["files"]
    assert isinstance(files, dict)
    found: dict[str, tuple[int, int]] = {}
    for path, data in files.items():
        package = package_of(path)
        if package is None:
            continue
        summary = data["summary"]
        covered = summary["covered_lines"] + summary.get("covered_branches", 0)
        parts = summary["num_statements"] + summary.get("num_branches", 0)
        before = found.get(package, (0, 0))
        found[package] = (before[0] + covered, before[1] + parts)
    return found


def judge(
    report: Mapping[str, object], limit: float = DEFAULT_LIMIT
) -> list[tuple[str, float | None, bool]]:
    """(package, percent or None when nothing was measured, passes) for every package."""
    measured = totals(report)
    rows: list[tuple[str, float | None, bool]] = []
    for package in PACKAGES:
        covered, parts = measured.get(package, (0, 0))
        if parts == 0:
            rows.append((package, None, False))
            continue
        percent = 100.0 * covered / parts
        rows.append((package, percent, percent >= limit))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", type=Path, default=Path("coverage.json"))
    parser.add_argument("--min", type=float, default=DEFAULT_LIMIT, dest="limit")
    args = parser.parse_args(argv)
    if not args.json.is_file():
        print(f"{args.json} is missing: run 'python -m coverage json' after the tests")
        return 1
    report = json.loads(args.json.read_text(encoding="utf8"))
    rows = judge(report, args.limit)
    print(f"{'package':<12}{'coverage':>10}")
    for package, percent, passes in rows:
        shown = "not measured" if percent is None else f"{percent:.1f}%"
        print(f"{package:<12}{shown:>12}  {'ok' if passes else 'BELOW ' + str(args.limit) + '%'}")
    failed = [package for package, _, passes in rows if not passes]
    if failed:
        print("below the limit: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
