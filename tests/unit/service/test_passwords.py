"""US-46: a password is stored as a salted scrypt hash, and a sign-in makes an expiring session."""

from __future__ import annotations

import pytest

from flowstat.repo.db import open_database
from flowstat.service.users import (
    MAX_PASSWORD_LENGTH,
    SESSION_HOURS,
    UserService,
    hash_password,
    later,
    verify_password,
)

T = "2026-11-08T10:00:00Z"
PASSWORD = "correct horse battery"
SALT = b"\x01" * 16
SESSION = "s" * 64


def service() -> UserService:
    users = UserService(open_database(":memory:"))
    users.add("dina", "designer", set(), "a" * 64, T)
    users.set_password("dina", PASSWORD, SALT)
    return users


def test_a_password_is_checked_against_its_hash_and_nothing_else() -> None:
    stored = hash_password(PASSWORD, SALT)
    assert stored.startswith("scrypt$16384$8$1$") and PASSWORD not in stored
    assert verify_password(stored, PASSWORD)
    assert not verify_password(stored, PASSWORD + "x")
    assert not verify_password(stored, "")


def test_the_same_password_with_another_salt_gives_another_hash() -> None:
    assert hash_password(PASSWORD, b"\x01" * 16) != hash_password(PASSWORD, b"\x02" * 16)


@pytest.mark.parametrize("stored", ["", "plain", "scrypt$x$8$1$00$00", "md5$1$1$1$00$00", "a$b"])
def test_a_stored_value_that_is_not_a_hash_never_matches(stored: str) -> None:
    assert not verify_password(stored, PASSWORD)


def test_only_the_hash_of_the_password_is_stored() -> None:
    users = service()
    dump = "\n".join(str(row) for row in users.store.db.execute("SELECT * FROM users"))
    assert PASSWORD not in dump and "scrypt$" in dump


@pytest.mark.parametrize("password", ["short", "x" * (MAX_PASSWORD_LENGTH + 1)])
def test_a_password_of_a_bad_length_is_refused(password: str) -> None:
    with pytest.raises(ValueError, match="characters"):
        service().set_password("dina", password, SALT)


def test_a_short_salt_is_refused() -> None:
    with pytest.raises(ValueError, match="salt"):
        service().set_password("dina", PASSWORD, b"\x01")


def test_a_sign_in_makes_a_session_that_authenticates_until_it_ends() -> None:
    users = service()
    user = users.login("dina", PASSWORD, SESSION, T)
    assert user is not None and user.name == "dina"
    assert users.authenticate(SESSION) is None  # without a clock only the real tokens count
    found = users.authenticate(SESSION, T)
    assert found is not None and found.role == "designer"
    last = later(T, SESSION_HOURS)
    assert users.authenticate(SESSION, "2026-11-08T17:59:59Z") is not None
    assert users.authenticate(SESSION, last) is None  # it ends at the moment it expires


def test_a_wrong_name_or_password_or_a_user_without_a_password_gets_no_session() -> None:
    users = service()
    users.add("olga", "operator", set(), "b" * 64, T)  # no password
    assert users.login("dina", "wrong password", SESSION, T) is None
    assert users.login("nobody", PASSWORD, SESSION, T) is None
    assert users.login("olga", PASSWORD, SESSION, T) is None
    assert users.login("dina", "x" * (MAX_PASSWORD_LENGTH + 1), SESSION, T) is None
    assert users.authenticate(SESSION, T) is None


def test_a_disabled_user_cannot_sign_in_and_its_session_stops() -> None:
    users = service()
    assert users.login("dina", PASSWORD, SESSION, T) is not None
    users.disable("dina")
    assert users.authenticate(SESSION, T) is None
    assert users.login("dina", PASSWORD, "t" * 64, T) is None


def test_a_logout_ends_the_session_and_leaves_the_real_token() -> None:
    users = service()
    users.login("dina", PASSWORD, SESSION, T)
    users.logout(SESSION)
    assert users.authenticate(SESSION, T) is None
    users.logout("a" * 64)  # the token made by ``add`` is not a session: nothing is removed
    assert users.authenticate("a" * 64) is not None
    users.logout("")  # nothing to do


def test_an_expired_session_is_removed_at_the_next_sign_in() -> None:
    users = service()
    users.login("dina", PASSWORD, SESSION, T)
    users.login("dina", PASSWORD, "t" * 64, "2026-11-09T10:00:00Z")
    rows = users.store.db.execute("SELECT count(*) FROM sessions").fetchone()
    assert rows == (1,)


def test_a_session_token_that_is_too_short_is_refused() -> None:
    with pytest.raises(ValueError, match="characters"):
        service().login("dina", PASSWORD, "short", T)


def test_later_adds_hours_across_a_day_and_keeps_the_format() -> None:
    assert later("2026-11-08T20:30:00Z", 8) == "2026-11-09T04:30:00Z"
    assert later(T, 0) == T
