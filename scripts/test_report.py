"""Make the test report of a run of the suite (DOC-7).

Usage: python scripts/test_report.py --junit junit.xml [--coverage coverage.json] [--date DATE]
                                     [--commit SHA] [--output FILE]

Input is what the tools write: ``pytest --junitxml=junit.xml`` and ``python -m coverage json``.
Output is a page of Markdown: the totals, the tests by area, every test that was skipped with the
reason, and the coverage of every package (line and branch counted together) with the packages of
C1 to C6 marked. The date and the commit are arguments, not read from the clock, so that the same
inputs give the same page. It uses the standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

STRICT = ("model", "dsl", "solver", "compiler", "analysis", "engine", "recovery", "migration")


def area(classname: str) -> str:
    """The part of the suite a test belongs to: ``unit/<package>`` or the name of its folder."""
    parts = classname.split(".")
    if len(parts) >= 3 and parts[0] == "tests" and parts[1] == "unit":
        return f"unit/{parts[2]}" if not parts[2].startswith("test_") else "unit"
    if len(parts) >= 2 and parts[0] == "tests":
        return parts[1]
    return parts[0]


def read_junit(path: Path) -> list[tuple[str, str, str, str]]:
    """(area, outcome, name, reason) of every test; outcome: passed, failed, skipped."""
    tests: list[tuple[str, str, str, str]] = []
    for case in ET.parse(path).getroot().iter("testcase"):
        outcome, reason = "passed", ""
        for child in case:
            if child.tag in ("failure", "error"):
                outcome, reason = "failed", child.get("message", "")
            elif child.tag == "skipped":
                outcome, reason = "skipped", child.get("message", "")
        name = f"{case.get('classname', '')}::{case.get('name', '')}"
        tests.append((area(case.get("classname", "")), outcome, name, reason))
    return tests


def package_coverage(report: Mapping[str, object]) -> dict[str, tuple[int, int]]:
    """Per package: parts covered and parts there are, statements and branches together."""
    files = report["files"]
    assert isinstance(files, dict)
    found: dict[str, tuple[int, int]] = {}
    for path, data in files.items():
        parts = path.replace("\\", "/").split("/")
        if "flowstat" not in parts[:-2]:
            continue
        package = parts[parts.index("flowstat") + 1]
        s = data["summary"]
        covered = s["covered_lines"] + s.get("covered_branches", 0)
        total = s["num_statements"] + s.get("num_branches", 0)
        before = found.get(package, (0, 0))
        found[package] = (before[0] + covered, before[1] + total)
    return found


def render(
    tests: list[tuple[str, str, str, str]],
    coverage: Mapping[str, object] | None,
    date: str,
    commit: str,
) -> str:
    totals = Counter(outcome for _, outcome, _, _ in tests)
    lines = [
        "# Test report",
        "",
        f"Made by `scripts/test_report.py` from the output of one run of the suite ({date}, commit "
        f"`{commit}`). The run is `python -m pytest --cov --junitxml=junit.xml`; nothing here was "
        "written by hand.",
        "",
        "## Totals",
        "",
        "| Passed | Failed | Skipped | Total |",
        "| ---: | ---: | ---: | ---: |",
        f"| {totals['passed']} | {totals['failed']} | {totals['skipped']} | {len(tests)} |",
        "",
        "## By area",
        "",
        "| Area | Passed | Failed | Skipped |",
        "| --- | ---: | ---: | ---: |",
    ]
    per: dict[str, Counter[str]] = {}
    for where, outcome, _, _ in tests:
        per.setdefault(where, Counter())[outcome] += 1
    for where in sorted(per):
        c = per[where]
        lines.append(f"| {where} | {c['passed']} | {c['failed']} | {c['skipped']} |")
    skipped = [(name, reason) for _, outcome, name, reason in tests if outcome == "skipped"]
    lines += ["", "## Skipped", ""]
    if skipped:
        lines += ["| Test | Reason |", "| --- | --- |"]
        lines += [f"| `{name}` | {reason or '-'} |" for name, reason in sorted(skipped)]
    else:
        lines.append("Nothing was skipped.")
    failed = [(name, reason) for _, outcome, name, reason in tests if outcome == "failed"]
    if failed:
        lines += ["", "## Failed", "", "| Test | Message |", "| --- | --- |"]
        lines += [f"| `{name}` | {reason[:120]} |" for name, reason in sorted(failed)]
    if coverage is not None:
        lines += [
            "",
            "## Coverage by package",
            "",
            "Statements and branches together. The gate is 90% for each package marked C1-C6 "
            "(`scripts/check_coverage.py`, NFR-09).",
            "",
            "| Package | Coverage | Gate |",
            "| --- | ---: | --- |",
        ]
        for package, (covered, total) in sorted(package_coverage(coverage).items()):
            percent = 100.0 * covered / total if total else 100.0
            gate = ("passes" if percent >= 90 else "BELOW 90%") if package in STRICT else "-"
            lines.append(f"| {package} | {percent:.1f}% | {gate} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--coverage", type=Path)
    parser.add_argument("--date", default="date not given")
    parser.add_argument("--commit", default="commit not given")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    coverage = json.loads(args.coverage.read_text("utf8")) if args.coverage else None
    page = render(read_junit(args.junit), coverage, args.date, args.commit)
    if args.output:
        args.output.write_text(page, encoding="utf8", newline="\n")
    else:
        sys.stdout.write(page)
    return 0


if __name__ == "__main__":
    sys.exit(main())
