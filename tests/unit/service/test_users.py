"""Tests of the user service: a token is shown once and only its hash is kept."""

from __future__ import annotations

import pytest

from flowstat.repo.db import open_database
from flowstat.service.users import ROLES, UserService, hash_token

T = "2026-11-08T10:00:00Z"
TOKEN = "a" * 64


def service() -> UserService:
    return UserService(open_database(":memory:"))


def test_a_token_authenticates_its_user_and_nobody_else() -> None:
    users = service()
    users.add("anara", "operator", {"chief_statistician"}, TOKEN, T)
    user = users.authenticate(TOKEN)
    assert user is not None and user.name == "anara" and user.role == "operator"
    assert users.authenticate("b" * 64) is None
    assert users.authenticate("") is None


def test_only_the_hash_is_stored() -> None:
    db = open_database(":memory:")
    UserService(db).add("anara", "operator", set(), TOKEN, T)
    dump = "\n".join(str(row) for row in db.execute("SELECT * FROM users"))
    assert TOKEN not in dump and hash_token(TOKEN) in dump
    assert hash_token(TOKEN) == hash_token(TOKEN) != hash_token(TOKEN + "x")
    assert len(hash_token(TOKEN)) == 64


def test_a_disabled_user_no_longer_authenticates() -> None:
    users = service()
    users.add("anara", "operator", set(), TOKEN, T)
    users.disable("anara")
    assert users.authenticate(TOKEN) is None


@pytest.mark.parametrize(
    ("name", "role", "token"),
    [
        ("Anara", "operator", TOKEN),  # names are lower case
        ("", "operator", TOKEN),
        ("anara", "root", TOKEN),  # no such role
        ("anara", "operator", "short"),  # a token must be long enough to be a secret
    ],
)
def test_a_user_with_a_bad_name_role_or_token_is_refused(name: str, role: str, token: str) -> None:
    with pytest.raises(ValueError):
        service().add(name, role, set(), token, T)


def test_the_roles_are_the_three_of_the_proposal() -> None:
    assert ROLES == ("admin", "designer", "operator")
