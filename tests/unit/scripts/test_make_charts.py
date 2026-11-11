"""scripts/make_charts.py draws the charts from the backlog, and they are up to date (DOC-7)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "make_charts", ROOT / "scripts" / "make_charts.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_charts"] = module
    spec.loader.exec_module(module)
    return module


charts = load()

BACKLOG = """\
| Milestone | SP | Stories | Goal | Status |
| --- | --- | --- | --- | --- |
| M0. Preparation | - | - | start | done |
| M1 (I1). One | 10 + 2 | US-01 | goal | done (1 Nov) |
| M2 (I2). Two | 8 | US-02 | goal | done |
| M3. Three | 5 | US-03, US-04 | goal | in progress |
| M9. Hardening | 20 | US-05 | goal | in progress |

| Story | Title | Function | SP | Priority | Milestone | Status |
| --- | --- | --- | --- | --- | --- | --- |
| US-01 | a | A1 | 10 | Must | M1 | done |
| US-02 | b | A1 | 8 | Must | M2 | done |
| US-03 | c | A1 | 3 | Must | M3 | done |
| US-04 | d | A1 | 2 | Must | M3 | not started |
| US-05 | e | A1 | 20 | Must | M9 | done |
"""


def test_a_milestone_that_is_done_delivered_its_whole_size_with_the_scope_that_came() -> None:
    m1 = charts.parse(BACKLOG)[0]
    assert (m1.key, m1.base, m1.added, m1.size, m1.delivered) == ("M1", 10, 2, 12, 12)


def test_a_milestone_that_is_not_done_delivered_the_stories_that_are_done() -> None:
    m3 = charts.parse(BACKLOG)[2]
    assert (m3.size, m3.delivered) == (5, 3)


def test_the_last_milestone_is_all_added_scope() -> None:
    m9 = charts.parse(BACKLOG)[3]
    assert (m9.base, m9.added, m9.delivered) == (0, 20, 20)


def test_the_burndown_starts_at_the_baseline_and_the_scope_steps_up_when_it_grew() -> None:
    points = charts.burndown(charts.parse(BACKLOG))
    assert points == [
        ("start", 23, 23),  # the baseline: 10 + 8 + 5
        ("M1", 25, 13),  # 2 added, 12 delivered
        ("M2", 25, 5),
        ("M3", 25, 2),
        ("M9", 45, 2),
    ]


def test_the_comment_lines_of_the_milestone_table_are_not_milestones() -> None:
    assert [m.key for m in charts.parse(BACKLOG)] == ["M1", "M2", "M3", "M9"]


def test_the_same_backlog_gives_the_same_bytes() -> None:
    assert charts.outputs(BACKLOG) == charts.outputs(BACKLOG)


def test_every_output_is_well_formed() -> None:
    made = charts.outputs(BACKLOG)
    assert set(made) == {"burndown.svg", "velocity.svg", "data.csv"}
    for name in ("burndown.svg", "velocity.svg"):
        assert made[name].startswith("<svg ") and made[name].rstrip().endswith("</svg>")
    assert made["data.csv"].splitlines()[0] == "milestone,base,added,size,delivered,scope,remaining"


def test_the_numbers_of_the_real_backlog_are_the_ones_of_the_proposal() -> None:
    real = charts.parse((ROOT / "docs" / "backlog.md").read_text(encoding="utf8"))
    assert [m.key for m in real] == [f"M{i}" for i in range(1, 10)]
    assert sum(m.base for m in real) == 203  # the baseline, the zero point of the burndown
    assert sum(m.added for m in real if m.key != "M9") == 11  # the parser (+6) and US-35 (+5)
    assert charts.burndown(real)[-2][1:] == (214, 0)  # after M8: the plan of 5 November, all done


def test_the_stories_of_a_finished_milestone_add_up_to_its_size() -> None:
    """The sizes in the table of milestones and in the table of stories say the same.

    M1 is left out: its 6 SP of parser and validator were added while it ran and have no row of
    their own in the table of stories.
    """
    text = (ROOT / "docs" / "backlog.md").read_text(encoding="utf8")
    per: dict[str, int] = {}
    for line in text.splitlines():
        if line.startswith(("| US-", "| DOC-")):
            c = charts.cells(line)
            per[c[5]] = per.get(c[5], 0) + int(c[3])
    for m in charts.parse(text):
        if m.key != "M1":
            assert per[m.key] == m.size, m.key
    assert per["M1"] + 6 == next(m.size for m in charts.parse(text) if m.key == "M1")


def test_the_charts_in_the_repository_are_what_the_backlog_gives_now() -> None:
    """After a change of docs/backlog.md run ``python scripts/make_charts.py``."""
    assert charts.main(["--check"]) == 0
