"""scripts/check_coverage.py judges every package of C1 to C6 by itself (NFR-09, DOC-7)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.architecture.rules import STRICT_PACKAGES

ROOT = Path(__file__).resolve().parents[3]


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_coverage", ROOT / "scripts" / "check_coverage.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_coverage"] = module
    spec.loader.exec_module(module)
    return module


gate = load()


def entry(covered: int, statements: int, covered_branches: int = 0, branches: int = 0) -> dict:  # type: ignore[type-arg]
    return {
        "summary": {
            "covered_lines": covered,
            "num_statements": statements,
            "covered_branches": covered_branches,
            "num_branches": branches,
        }
    }


def report(**per_package: tuple[int, int]) -> dict[str, object]:
    """A report where each package is one file of ``covered`` of ``parts`` statements."""
    return {
        "files": {
            f"src/flowstat/{name}/x.py": entry(covered, parts)
            for name, (covered, parts) in per_package.items()
        }
    }


FULL = {name: (100, 100) for name in gate.PACKAGES}


def test_the_packages_are_those_the_architecture_test_calls_strict() -> None:
    assert set(gate.PACKAGES) == STRICT_PACKAGES


def test_a_package_of_a_path_is_found_in_both_kinds_of_separator() -> None:
    assert gate.package_of("src/flowstat/dsl/reader.py") == "dsl"
    assert gate.package_of("src\\flowstat\\engine\\scheduler.py") == "engine"
    assert gate.package_of("flowstat/model/events.py") == "model"
    assert gate.package_of("src/flowstat/__init__.py") is None
    assert gate.package_of("tests/unit/dsl/test_reader.py") is None


def test_lines_and_branches_are_counted_together() -> None:
    data = {"files": {"src/flowstat/dsl/a.py": entry(8, 10, 1, 10)}}
    assert gate.totals(data) == {"dsl": (9, 20)}


def test_every_package_at_the_limit_passes() -> None:
    rows = gate.judge(report(**{**FULL, "dsl": (90, 100)}))
    assert all(passes for _, _, passes in rows)


def test_a_package_below_the_limit_fails_even_when_the_others_are_perfect() -> None:
    rows = {p: (pct, ok) for p, pct, ok in gate.judge(report(**{**FULL, "migration": (89, 100)}))}
    assert rows["migration"] == (89.0, False)
    assert [p for p, (_, ok) in rows.items() if not ok] == ["migration"]


def test_a_package_that_was_not_measured_fails() -> None:
    partial = {k: v for k, v in FULL.items() if k != "solver"}
    rows = {p: (pct, ok) for p, pct, ok in gate.judge(report(**partial))}
    assert rows["solver"] == (None, False)


def test_files_of_other_packages_do_not_count() -> None:
    data = report(**FULL)
    data["files"]["src/flowstat/api/app.py"] = entry(0, 1000)  # type: ignore[index]
    assert all(ok for _, _, ok in gate.judge(data))


@pytest.mark.parametrize(("covered", "code"), [(100, 0), (50, 1)])
def test_the_command_exits_with_the_verdict(covered: int, code: int, tmp_path: Path) -> None:
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(report(**{**FULL, "engine": (covered, 100)})), encoding="utf8")
    assert gate.main(["--json", str(path)]) == code


def test_the_command_says_so_when_there_is_no_report(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    assert gate.main(["--json", str(tmp_path / "none.json")]) == 1
    assert "coverage json" in capsys.readouterr().out
