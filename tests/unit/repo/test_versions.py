"""Tests of the version store (A1): published versions never change."""

from __future__ import annotations

import sqlite3

import pytest

from flowstat.model.versions import VersionStatus
from flowstat.repo.db import open_database
from flowstat.repo.versions import AlreadyPublishedError, VersionNotFoundError, VersionStore

T1, T2, T3 = "2026-11-05T10:00:00Z", "2026-11-05T11:00:00Z", "2026-11-05T12:00:00Z"


def store() -> VersionStore:
    return VersionStore(open_database(":memory:"))


def test_first_save_creates_draft_version_one() -> None:
    s = store()
    v = s.save("demo", "text1", "{}", T1)
    assert (v.name, v.number, v.status) == ("demo", 1, VersionStatus.DRAFT)
    assert v.text == "text1" and v.created_at == T1 and v.published_at is None


def test_saving_again_edits_the_same_draft() -> None:
    s = store()
    s.save("demo", "a", "{}", T1)
    v = s.save("demo", "b", "{}", T2)
    assert v.number == 1 and v.text == "b"
    assert [x.number for x in s.versions("demo")] == [1]


def test_publish_marks_the_version_and_stores_the_snapshot() -> None:
    s = store()
    s.save("demo", "a", "{}", T1)
    v = s.publish("demo", 1, '{"regions": []}', T2)
    assert v.status is VersionStatus.PUBLISHED and v.published_at == T2
    assert v.catalogue == '{"regions": []}'
    assert s.get("demo", 1) == v


def test_us01_saving_after_publication_creates_the_next_draft_and_leaves_the_old_one() -> None:
    """Given v2 is published, when I save edits, then v3 is a draft and v2 is unchanged."""
    s = store()
    s.save("demo", "v1", "{}", T1)
    s.publish("demo", 1, "{}", T1)
    s.save("demo", "v2", "{}", T2)
    published_v2 = s.publish("demo", 2, '{"snap": 2}', T2)
    v3 = s.save("demo", "v3 edit", '{"snap": 3}', T3)
    assert (v3.number, v3.status) == (3, VersionStatus.DRAFT)
    assert s.get("demo", 2) == published_v2
    assert s.get("demo", 2).text == "v2"
    assert [(x.number, x.status) for x in s.versions("demo")] == [
        (1, VersionStatus.PUBLISHED), (2, VersionStatus.PUBLISHED), (3, VersionStatus.DRAFT),
    ]  # fmt: skip


def test_a_published_version_cannot_be_published_again() -> None:
    s = store()
    s.save("demo", "a", "{}", T1)
    s.publish("demo", 1, "{}", T1)
    with pytest.raises(AlreadyPublishedError):
        s.publish("demo", 1, "{}", T2)


def test_the_database_itself_refuses_to_change_a_published_version() -> None:
    db = open_database(":memory:")
    s = VersionStore(db)
    s.save("demo", "a", "{}", T1)
    s.publish("demo", 1, "{}", T1)
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        db.execute("UPDATE versions SET text = 'hacked' WHERE name = 'demo'")
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        db.execute("DELETE FROM versions WHERE name = 'demo'")
    assert s.get("demo", 1).text == "a"


def test_a_draft_may_be_deleted_by_the_database_rules() -> None:
    db = open_database(":memory:")
    s = VersionStore(db)
    s.save("demo", "a", "{}", T1)
    db.execute("DELETE FROM versions WHERE name = 'demo'")
    assert s.latest("demo") is None


def test_unknown_version_and_name() -> None:
    s = store()
    with pytest.raises(VersionNotFoundError):
        s.get("nope", 1)
    with pytest.raises(VersionNotFoundError):
        s.publish("nope", 1, "{}", T1)
    assert s.latest("nope") is None
    assert s.versions("nope") == []


def test_workflows_are_independent_and_listed() -> None:
    s = store()
    s.save("b", "x", "{}", T1)
    s.save("a", "y", "{}", T1)
    s.publish("a", 1, "{}", T1)
    assert s.names() == ["a", "b"]
    assert s.save("a", "z", "{}", T2).number == 2
    assert s.latest("b") is not None and s.latest("b").number == 1  # type: ignore[union-attr]


def test_data_survives_reopening_a_file_database(tmp_path: object) -> None:
    path = str(tmp_path / "flowstat.db")  # type: ignore[operator]
    db = open_database(path)
    VersionStore(db).save("demo", "kept", "{}", T1)
    db.close()
    again = VersionStore(open_database(path))
    assert again.get("demo", 1).text == "kept"


def test_the_store_does_not_read_the_clock() -> None:
    """Time is passed in (deterministic mode): the same calls give the same rows."""
    one, two = store(), store()
    for s in (one, two):
        s.save("demo", "a", "{}", T1)
        s.publish("demo", 1, "{}", T2)
    assert one.get("demo", 1) == two.get("demo", 1)
