"""The sample forms of scripts/make_forms.py (US-50): the sets are what they say they are."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest

from flowstat.actions.base import ActionContext
from flowstat.actions.statguard import violations
from flowstat.actions.xlsx import read_form

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def make_forms() -> ModuleType:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        return importlib.import_module("make_forms")
    finally:
        sys.path.remove(str(ROOT / "scripts"))


def rows(folder: Path, region: str) -> list[object]:
    context = ActionContext("t", "t", folder, None)
    found = read_form({"path": f"{region}.xlsx"}, context)["rows"]
    assert isinstance(found, list)
    return found


def test_the_three_sets_have_the_forms_they_are_named_for(
    make_forms: ModuleType, tmp_path: Path
) -> None:
    sets = make_forms.write_sets(tmp_path)
    names = {name: sorted(p.stem for p in paths) for name, paths in sets.items()}
    assert names["good"] == sorted(make_forms.REGIONS)
    assert names["with-a-mistake"] == sorted(make_forms.REGIONS)
    assert names["six-regions"] == sorted(set(make_forms.REGIONS) - set(make_forms.MISSING))
    assert len(names["six-regions"]) == 6  # fewer than the seven that the consolidation needs


def test_the_good_forms_keep_every_rule_and_the_mistake_is_one_and_in_one_place(
    make_forms: ModuleType, tmp_path: Path
) -> None:
    make_forms.write_sets(tmp_path)
    for region in make_forms.REGIONS:
        assert violations(rows(tmp_path / "good", region)) == []
        found = violations(rows(tmp_path / "with-a-mistake", region))
        assert (len(found), region == make_forms.MISTAKE_IN) in ((0, False), (1, True))
    assert "value is negative" in violations(rows(tmp_path / "with-a-mistake", "BAT"))[0]


def test_the_figures_are_invented_and_the_same_at_every_call(
    make_forms: ModuleType, tmp_path: Path
) -> None:
    make_forms.write_sets(tmp_path / "a")
    make_forms.write_sets(tmp_path / "b")
    for region in make_forms.REGIONS:
        assert rows(tmp_path / "a" / "good", region) == rows(tmp_path / "b" / "good", region)
    regions = [rows(tmp_path / "a" / "good", r)[0] for r in make_forms.REGIONS]
    assert len({str(r) for r in regions}) == 9  # each region has figures of its own
