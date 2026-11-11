"""scripts/test_report.py makes the page from the junit file and the coverage report (DOC-7)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "test_report", ROOT / "scripts" / "test_report.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["test_report"] = module
    spec.loader.exec_module(module)
    return module


report = load()

JUNIT = """<?xml version="1.0"?>
<testsuites><testsuite name="pytest">
<testcase classname="tests.unit.engine.test_scheduler" name="test_a" time="0.1"/>
<testcase classname="tests.unit.engine.test_scheduler" name="test_b" time="0.1">
  <failure message="assert 1 == 2">trace</failure></testcase>
<testcase classname="tests.crash.test_every_event" name="test_c" time="0.1"/>
<testcase classname="tests.acceptance.test_us30" name="test_d" time="0.1">
  <skipped message="no Docker daemon here"/></testcase>
<testcase classname="tests.unit.test_smoke" name="test_e" time="0.1"/>
</testsuite></testsuites>
"""


def covered(**per_package: tuple[int, int]) -> dict[str, object]:
    return {
        "files": {
            f"src/flowstat/{p}/x.py": {
                "summary": {
                    "covered_lines": c,
                    "num_statements": t,
                    "covered_branches": 0,
                    "num_branches": 0,
                }
            }
            for p, (c, t) in per_package.items()
        }
    }


def results(tmp_path: Path) -> list[tuple[str, str, str, str]]:
    path = tmp_path / "junit.xml"
    path.write_text(JUNIT, encoding="utf8")
    found: list[tuple[str, str, str, str]] = report.read_junit(path)
    return found


def test_a_test_belongs_to_the_package_it_tests_or_to_the_folder_of_its_kind(
    tmp_path: Path,
) -> None:
    assert [t[0] for t in results(tmp_path)] == [
        "unit/engine",
        "unit/engine",
        "crash",
        "acceptance",
        "unit",
    ]


def test_the_outcomes_are_read_with_their_reasons(tmp_path: Path) -> None:
    assert [(t[1], t[3]) for t in results(tmp_path)] == [
        ("passed", ""),
        ("failed", "assert 1 == 2"),
        ("passed", ""),
        ("skipped", "no Docker daemon here"),
        ("passed", ""),
    ]


def test_the_page_has_the_totals_the_areas_the_skips_and_the_failures(tmp_path: Path) -> None:
    page = report.render(results(tmp_path), None, "11 November 2026", "abc1234")
    assert "| 3 | 1 | 1 | 5 |" in page
    assert "| unit/engine | 1 | 1 | 0 |" in page
    assert "`tests.acceptance.test_us30::test_d` | no Docker daemon here" in page
    assert "## Failed" in page and "assert 1 == 2" in page
    assert "11 November 2026" in page and "`abc1234`" in page


def test_a_run_without_a_skip_says_so(tmp_path: Path) -> None:
    only = [t for t in results(tmp_path) if t[1] == "passed"]
    page = report.render(only, None, "d", "c")
    assert "Nothing was skipped." in page and "## Failed" not in page


def test_the_coverage_of_a_package_of_c1_to_c6_is_judged_and_the_others_are_only_shown(
    tmp_path: Path,
) -> None:
    data = covered(engine=(95, 100), solver=(80, 100), api=(10, 100))
    page = report.render(results(tmp_path), data, "d", "c")
    assert "| engine | 95.0% | passes |" in page
    assert "| solver | 80.0% | BELOW 90% |" in page
    assert "| api | 10.0% | - |" in page


def test_the_same_inputs_give_the_same_page(tmp_path: Path) -> None:
    a = report.render(results(tmp_path), covered(dsl=(9, 10)), "d", "c")
    b = report.render(results(tmp_path), covered(dsl=(9, 10)), "d", "c")
    assert a == b


def test_the_command_writes_the_page_to_a_file(tmp_path: Path) -> None:
    (tmp_path / "junit.xml").write_text(JUNIT, encoding="utf8")
    out = tmp_path / "report.md"
    code = report.main(["--junit", str(tmp_path / "junit.xml"), "--output", str(out)])
    assert code == 0 and out.read_text(encoding="utf8").startswith("# Test report")
