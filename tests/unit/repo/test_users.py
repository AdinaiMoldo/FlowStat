"""Tests of the user table (A3): a role, titles, and only the hash of a token."""

from __future__ import annotations

import sqlite3

import pytest

from flowstat.repo.db import open_database
from flowstat.repo.users import UserExistsError, UserNotFoundError, UserStore

T = "2026-11-08T10:00:00Z"


def store() -> UserStore:
    return UserStore(open_database(":memory:"))


def test_a_user_is_found_by_the_hash_of_its_token() -> None:
    users = store()
    added = users.add("anara", "designer", frozenset({"chief_statistician"}), "h1", T)
    found = users.by_token_hash("h1")
    assert found == added
    assert (found.name, found.role) == ("anara", "designer")
    assert found.titles == frozenset({"chief_statistician"})
    assert users.by_token_hash("other") is None


def test_a_name_and_a_token_belong_to_one_user() -> None:
    users = store()
    users.add("anara", "operator", frozenset(), "h1", T)
    with pytest.raises(UserExistsError):
        users.add("anara", "admin", frozenset(), "h2", T)
    with pytest.raises(UserExistsError):
        users.add("bakyt", "operator", frozenset(), "h1", T)


def test_a_disabled_user_cannot_be_found_and_can_be_listed() -> None:
    users = store()
    users.add("anara", "operator", frozenset(), "h1", T)
    users.add("bakyt", "admin", frozenset(), "h2", T)
    users.disable("anara")
    assert users.by_token_hash("h1") is None
    assert [u.name for u in users.all()] == ["anara", "bakyt"]
    assert [u.name for u in users.all() if u.disabled] == ["anara"]
    with pytest.raises(UserNotFoundError):
        users.disable("nobody")


def test_the_database_refuses_a_role_that_does_not_exist() -> None:
    db = open_database(":memory:")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO users (name, role, titles, token_hash, created_at)"
            " VALUES ('x', 'root', '[]', 'h', 't')"
        )


def test_titles_survive_the_round_trip_in_order_independent_form() -> None:
    users = store()
    users.add("anara", "operator", frozenset({"b", "a"}), "h1", T)
    found = users.by_token_hash("h1")
    assert found is not None and found.titles == frozenset({"a", "b"})
