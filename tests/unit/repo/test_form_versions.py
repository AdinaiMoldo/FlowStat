"""US-55: the versions of the forms are kept in a table, numbered for a region and a quarter."""

from __future__ import annotations

import pytest

from flowstat.model.diagnostics import Severity
from flowstat.model.rules import Finding
from flowstat.repo.db import open_database
from flowstat.repo.form_versions import FormVersionNotFoundError, FormVersionRepo


@pytest.fixture
def repo() -> FormVersionRepo:
    return FormVersionRepo(open_database(":memory:"))


def add(repo: FormVersionRepo, region: str = "BAT", quarter: str = "2025Q1", n: int = 1):  # type: ignore[no-untyped-def]
    found = (
        Finding(
            "VAL-RANGE-001",
            Severity.ERROR,
            "C5: negative",
            region,
            "f",
            "S",
            5,
            "C5",
            "I04",
            "-1",
            "0+",
        ),
    )
    return repo.add(
        region,
        quarter,
        f"{n:064x}",
        100 + n,
        5,
        "olga",
        f"2026-11-0{n}T10:00:00Z",
        "basic",
        found,
        b"xlsx" * n,
    )


def test_versions_are_numbered_for_a_region_and_a_quarter(repo: FormVersionRepo) -> None:
    assert [add(repo, n=n).number for n in (1, 2, 3)] == [1, 2, 3]
    assert add(repo, "OSC").number == 1  # another region starts again
    assert add(repo, "BAT", "2025Q2").number == 1  # so does another quarter


def test_what_was_stored_comes_back_with_its_findings(repo: FormVersionRepo) -> None:
    add(repo, n=1)
    got = repo.get("BAT", "2025Q1", 1)
    assert (got.by, got.at, got.size, got.rows, got.rules) == (
        "olga",
        "2026-11-01T10:00:00Z",
        101,
        5,
        "basic",
    )
    assert got.findings[0].code == "VAL-RANGE-001" and got.errors == 1
    assert repo.content("BAT", "2025Q1", 1) == b"xlsx"


def test_the_current_version_is_the_latest_one(repo: FormVersionRepo) -> None:
    assert repo.current("BAT", "2025Q1") is None
    add(repo, n=1)
    add(repo, n=2)
    current = repo.current("BAT", "2025Q1")
    assert current is not None and current.number == 2
    assert [v.number for v in repo.all("BAT", "2025Q1")] == [2, 1]  # the newest first
    assert repo.count("BAT", "2025Q1") == 2 and repo.count("BAT", "2025Q2") == 0


def test_a_removed_form_has_no_current_version_but_keeps_its_history(repo: FormVersionRepo) -> None:
    add(repo, n=1)
    add(repo, n=2)
    assert repo.mark_removed("BAT", "2025Q1", "2026-11-09T10:00:00Z", "ada") is True
    assert repo.current("BAT", "2025Q1") is None
    gone = repo.get("BAT", "2025Q1", 2)
    assert (gone.removed_at, gone.removed_by) == ("2026-11-09T10:00:00Z", "ada")
    assert repo.get("BAT", "2025Q1", 1).removed_at is None  # only the current one was removed
    assert (
        repo.mark_removed("BAT", "2025Q1", "2026-11-09T11:00:00Z", "ada") is False
    )  # nothing left
    assert add(repo, n=3).number == 3 and repo.current("BAT", "2025Q1") is not None


def test_a_version_that_is_not_there_is_not_found(repo: FormVersionRepo) -> None:
    with pytest.raises(FormVersionNotFoundError):
        repo.get("BAT", "2025Q1", 1)
    with pytest.raises(FormVersionNotFoundError):
        repo.content("BAT", "2025Q1", 1)
