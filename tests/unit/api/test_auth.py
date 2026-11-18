"""US-46: sign in with a name and a password, see who is signed in, sign out."""

from __future__ import annotations

import itertools
from dataclasses import replace
from pathlib import Path

import pytest

from flowstat.api.auth import ATTEMPTS, WINDOW
from flowstat.repo.db import open_database
from flowstat.service.permissions import PERMISSIONS
from flowstat.service.users import UserService
from tests.unit.api.helpers import Env, make_env

PASSWORD = "correct horse battery"


@pytest.fixture
def env(tmp_path: Path) -> Env:
    made = make_env(tmp_path)
    users = UserService(open_database(made.config.db_path))
    for name in ("ada", "dina", "olga"):
        users.set_password(name, PASSWORD, name.encode().ljust(16, b"."))
    return made


def sign_in(env: Env, name: str = "dina", password: str = PASSWORD):  # type: ignore[no-untyped-def]
    return env.client.post("/login", json={"name": name, "password": password})


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def set_clock(env: Env, now: str | None = None, seconds: float | None = None) -> None:
    """Move the clock of the running app (``clock`` is the text of the time, ``time`` seconds)."""
    config = env.client.app.state.config  # type: ignore[attr-defined]
    if now is not None:
        config = replace(config, clock=lambda: now)
    if seconds is not None:
        counter = itertools.count()
        config = replace(config, time=lambda: seconds + next(counter) * 0.001)
    env.client.app.state.config = config  # type: ignore[attr-defined]


def test_a_sign_in_gives_a_token_that_works_as_the_user(env: Env) -> None:
    reply = sign_in(env)
    assert reply.status_code == 200
    body = reply.json()
    assert (body["name"], body["role"], body["titles"]) == ("dina", "designer", [])
    assert body["expires_at"] == "2026-11-08T18:00:00Z"  # 8 hours after the clock of the test
    assert len(body["token"]) == 64
    token = body["token"]
    assert env.client.get("/workflows", headers=bearer(token)).status_code == 200
    # the role of the user still decides: a designer may not answer an approval
    denied = env.client.post(
        "/runs/r-0001/decisions", headers=bearer(token), json={"step": "a", "decision": "approved"}
    )
    assert denied.status_code == 403 and denied.json()["errors"][0]["code"] == "E-AUTH-002"


def test_the_titles_of_the_user_come_with_the_session(env: Env) -> None:
    body = sign_in(env, "olga").json()
    assert body["role"] == "operator" and body["titles"] == ["chief_statistician"]


@pytest.mark.parametrize(
    ("name", "password"),
    [("dina", "wrong password!"), ("nobody", PASSWORD), ("kira", PASSWORD), ("dina", "")],
)
def test_a_wrong_name_or_password_is_one_and_the_same_answer(
    env: Env, name: str, password: str
) -> None:
    reply = sign_in(env, name, password)  # kira has no password
    assert reply.status_code == 401
    assert reply.json()["errors"] == [
        {"code": "E-AUTH-004", "message": "the name or the password is wrong"}
    ]


def test_a_request_with_other_fields_or_none_is_refused(env: Env) -> None:
    assert env.client.post("/login", json={"name": "dina"}).status_code == 422
    body = {"name": "dina", "password": PASSWORD, "role": "admin"}
    assert env.client.post("/login", json=body).status_code == 422


def test_after_several_failures_even_the_right_password_waits(env: Env) -> None:
    for _ in range(ATTEMPTS):
        assert sign_in(env, "dina", "wrong password!").status_code == 401
    blocked = sign_in(env)
    assert blocked.status_code == 429
    assert blocked.json()["errors"][0]["code"] == "E-AUTH-003"
    assert sign_in(env, "olga").status_code == 200  # another name is not blocked
    set_clock(env, seconds=WINDOW + 1000)
    assert sign_in(env).status_code == 200  # the window has passed


def test_a_good_sign_in_clears_the_count(env: Env) -> None:
    for _ in range(ATTEMPTS - 1):
        sign_in(env, "dina", "wrong password!")
    assert sign_in(env).status_code == 200
    for _ in range(ATTEMPTS - 1):
        assert sign_in(env, "dina", "wrong password!").status_code == 401  # not blocked yet


def test_an_unknown_name_is_counted_too_so_that_names_cannot_be_tried_for_free(env: Env) -> None:
    for _ in range(ATTEMPTS):
        sign_in(env, "ghost", PASSWORD)
    assert sign_in(env, "ghost", PASSWORD).status_code == 429


def test_a_session_ends_when_it_expires(env: Env) -> None:
    token = sign_in(env).json()["token"]
    set_clock(env, "2026-11-08T17:59:59Z")
    assert env.client.get("/me", headers=bearer(token)).status_code == 200
    set_clock(env, "2026-11-08T18:00:00Z")
    gone = env.client.get("/me", headers=bearer(token))
    assert gone.status_code == 401 and gone.json()["errors"][0]["code"] == "E-AUTH-001"


def test_me_says_who_is_signed_in_and_what_the_role_may_do(env: Env) -> None:
    token = sign_in(env).json()["token"]
    mine = env.client.get("/me", headers=bearer(token)).json()
    assert mine["name"] == "dina" and mine["role"] == "designer"
    expected = sorted(
        p for p, roles in PERMISSIONS.items() if "designer" in roles and p != "events:ingest"
    )
    assert mine["permissions"] == expected
    assert "runs:decide" not in mine["permissions"] and "workflows:write" in mine["permissions"]
    assert "token" not in mine  # nothing secret in the answer


def test_me_also_works_with_a_token_made_by_the_command_line(env: Env) -> None:
    reply = env.call("operator", "GET", "/me")
    assert reply.status_code == 200 and reply.json()["name"] == "olga"
    assert env.call(None, "GET", "/me").status_code == 401


def test_a_sign_out_ends_the_session_and_only_that_one(env: Env) -> None:
    first = sign_in(env).json()["token"]
    second = sign_in(env).json()["token"]
    assert first != second
    assert env.client.post("/logout", headers=bearer(first)).status_code == 204
    assert env.client.get("/me", headers=bearer(first)).status_code == 401
    assert env.client.get("/me", headers=bearer(second)).status_code == 200


def test_a_sign_out_with_a_token_of_the_command_line_leaves_that_token_working(env: Env) -> None:
    assert env.call("designer", "POST", "/logout").status_code == 204
    assert env.call("designer", "GET", "/me").status_code == 200
    assert env.call(None, "POST", "/logout").status_code == 401


def test_a_disabled_user_loses_the_open_session(env: Env) -> None:
    token = sign_in(env).json()["token"]
    UserService(open_database(env.config.db_path)).disable("dina")
    assert env.client.get("/me", headers=bearer(token)).status_code == 401
    assert sign_in(env).status_code == 401


def test_a_session_token_is_not_accepted_as_the_key_of_a_deployment(env: Env) -> None:
    token = sign_in(env).json()["token"]
    reply = env.client.post(
        "/deployments/d1/events", headers=bearer(token), json={"run": {}, "events": []}
    )
    assert reply.status_code in (401, 403)  # not accepted as the key of a deployment


def test_only_the_hash_of_a_session_token_is_stored(env: Env) -> None:
    token = sign_in(env).json()["token"]
    db = open_database(env.config.db_path)
    dump = "\n".join(str(row) for row in db.execute("SELECT * FROM sessions"))
    assert token not in dump and len(dump) > 0
