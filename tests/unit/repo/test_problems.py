"""US-56: the problems of the forms and their history are kept in tables."""

from __future__ import annotations

from typing import Any

import pytest

from flowstat.model import problems as pr
from flowstat.model.problems import Event, Problem
from flowstat.repo.db import open_database
from flowstat.repo.problems import ProblemNotFoundError, ProblemRepo


@pytest.fixture
def repo() -> ProblemRepo:
    return ProblemRepo(open_database(":memory:"))


def make(
    pid: str = "p1",
    region: str = "BAT",
    quarter: str = "2025Q1",
    key: str = "VAL-X|I04",
    **c: object,
) -> Problem:
    base: dict[str, object] = dict(
        id=pid, region=region, quarter=quarter, key=key, code="VAL-X", indicator="I04", cell="C5",
        message="C5: bad", actual="-1", expected="0+", status=pr.OPEN, assignee="olga",
        opened_in=1, seen_in=1, created_at="2026-11-08T10:00:00Z",
        updated_at="2026-11-08T10:00:00Z",
    )  # fmt: skip
    base.update(c)
    return Problem(**base)  # type: ignore[arg-type]


def test_a_problem_is_stored_and_read_back(repo: ProblemRepo) -> None:
    repo.add(make())
    assert repo.get("p1") == make()
    with pytest.raises(ProblemNotFoundError):
        repo.get("nope")


def test_the_problems_of_a_form_are_found_by_region_and_quarter(repo: ProblemRepo) -> None:
    repo.add(make("a", key="K1"))
    repo.add(make("b", key="K2"))
    repo.add(make("c", region="OSC"))
    repo.add(make("d", quarter="2025Q2"))
    assert [p.id for p in repo.of_form("BAT", "2025Q1")] == ["a", "b"]


def test_a_form_has_one_problem_for_one_mistake(repo: ProblemRepo) -> None:
    repo.add(make("a"))
    with pytest.raises(ValueError, match="already"):
        repo.add(make("b"))  # the same region, quarter and key


def test_a_problem_is_updated_where_it_stands(repo: ProblemRepo) -> None:
    repo.add(make())
    repo.update(
        "p1",
        status=pr.REOPENED,
        cell="C6",
        message="C6: bad",
        actual="-3",
        seen_in=2,
        at="2026-11-09T10:00:00Z",
    )
    got = repo.get("p1")
    assert (got.status, got.cell, got.actual, got.seen_in, got.updated_at) == (
        pr.REOPENED,
        "C6",
        "-3",
        2,
        "2026-11-09T10:00:00Z",
    )
    assert (got.opened_in, got.created_at, got.assignee) == (
        1,
        "2026-11-08T10:00:00Z",
        "olga",
    )  # untouched
    repo.update("p1", status=pr.RESOLVED, at="2026-11-10T10:00:00Z")
    assert repo.get("p1").cell == "C6"  # only what was given changes


def test_a_problem_can_be_assigned_and_unassigned(repo: ProblemRepo) -> None:
    repo.add(make())
    repo.set_assignee("p1", "kira", "2026-11-09T10:00:00Z")
    assert repo.get("p1").assignee == "kira"
    repo.set_assignee("p1", None, "2026-11-09T11:00:00Z")
    assert repo.get("p1").assignee is None and repo.get("p1").updated_at == "2026-11-09T11:00:00Z"


def test_the_listing_filters_by_quarter_region_status_and_assignee(repo: ProblemRepo) -> None:
    repo.add(make("a", key="K1"))
    repo.add(make("b", key="K2", status=pr.RESOLVED))
    repo.add(make("c", region="OSC", assignee="kira"))
    repo.add(make("d", quarter="2025Q2", status=pr.REOPENED))

    def ids(**f: Any) -> list[str]:
        return [p.id for p in repo.find(**f)]

    assert ids() == ["a", "b", "c", "d"]
    assert ids(quarter="2025Q1") == ["a", "b", "c"]
    assert ids(region="OSC") == ["c"]
    assert ids(active=True) == ["a", "c", "d"]
    assert ids(status=pr.RESOLVED) == ["b"]
    assert ids(assignee="kira") == ["c"]


def test_the_history_keeps_its_order_and_cannot_be_changed(repo: ProblemRepo) -> None:
    repo.add(make())
    repo.add_event(Event("p1", "2026-11-08T10:00:00Z", "system", "OPENED", "C5: bad", 1))
    repo.add_event(Event("p1", "2026-11-08T10:05:00Z", "ada", "COMMENTED", "looking", None))
    repo.add_event(Event("p1", "2026-11-09T10:00:00Z", "system", "RESOLVED", "", 2))
    assert [(e.kind, e.by, e.version) for e in repo.events("p1")] == [
        ("OPENED", "system", 1),
        ("COMMENTED", "ada", None),
        ("RESOLVED", "system", 2),
    ]
    with pytest.raises(Exception, match="append-only"):
        repo.db.execute("UPDATE problem_events SET text = 'x'")
    with pytest.raises(Exception, match="append-only"):
        repo.db.execute("DELETE FROM problem_events")
