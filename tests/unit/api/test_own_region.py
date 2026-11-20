"""US-64: an operator of a regional office sends and removes only the forms of that office."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from flowstat.repo.db import open_database
from flowstat.service.users import UserService
from tests.unit.actions.helpers import FIVE_ROWS, GOOD_ROWS, Row, write_form
from tests.unit.api.helpers import Env, T, headers, make_env

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
OSH_TOKEN = "e5" * 32  # aibek, the operator of the office of Osh oblast
TWO_TOKEN = "e6" * 32  # nurlan, an operator of two offices
BAD: list[Row] = [*FIVE_ROWS[:3], ("I04", "Deaths", -2.5), FIVE_ROWS[4]]


@pytest.fixture
def env(tmp_path: Path) -> Env:
    made = make_env(tmp_path)
    db = open_database(made.config.db_path)
    users = UserService(db)
    users.add("aibek", "operator", set(), OSH_TOKEN, T, regions={"OSH"})
    users.add("nurlan", "operator", set(), TWO_TOKEN, T, regions={"NAR", "TAL"})
    db.close()
    return made


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def put(env: Env, auth: dict[str, str], region: str, tmp_path: Path, rows: list[Row] = GOOD_ROWS):  # type: ignore[no-untyped-def]
    content = write_form(tmp_path / "made.xlsx", rows).read_bytes()
    return env.client.put(
        f"/forms/{region}/2025Q1", content=content, headers={**auth, "Content-Type": XLSX}
    )


def refused(reply: Any, region: str) -> None:
    assert reply.status_code == 403, reply.text
    error = reply.json()["errors"][0]
    assert error["code"] == "E-AUTH-005" and region in error["message"]


def test_an_operator_of_an_office_sends_and_removes_the_form_of_that_office(
    env: Env, tmp_path: Path
) -> None:
    assert put(env, bearer(OSH_TOKEN), "OSH", tmp_path).status_code == 200
    gone = env.client.delete("/forms/OSH/2025Q1", headers=bearer(OSH_TOKEN))
    assert gone.status_code == 204


def test_he_may_not_send_the_form_of_another_office(env: Env, tmp_path: Path) -> None:
    refused(put(env, bearer(OSH_TOKEN), "BIS", tmp_path), "BIS")
    assert not (env.config.data_dir / "BIS").exists()


def test_he_may_not_remove_the_form_of_another_office_and_it_stays(
    env: Env, tmp_path: Path
) -> None:
    assert put(env, headers("admin"), "BIS", tmp_path).status_code == 200
    refused(env.client.delete("/forms/BIS/2025Q1", headers=bearer(OSH_TOKEN)), "BIS")
    assert (env.config.data_dir / "BIS" / "2025Q1.xlsx").exists()
    listed = env.client.get("/forms?quarter=2025Q1", headers=bearer(OSH_TOKEN)).json()["forms"]
    assert [f["region"] for f in listed if f["received"]] == ["BIS"]  # reading stays open


def test_an_operator_of_two_offices_has_both_and_no_third(env: Env, tmp_path: Path) -> None:
    assert put(env, bearer(TWO_TOKEN), "NAR", tmp_path).status_code == 200
    assert put(env, bearer(TWO_TOKEN), "TAL", tmp_path).status_code == 200
    refused(put(env, bearer(TWO_TOKEN), "OSH", tmp_path), "OSH")


def test_the_admin_and_an_operator_of_the_central_office_are_not_bound_to_an_office(
    env: Env, tmp_path: Path
) -> None:
    assert put(env, headers("admin"), "CHU", tmp_path).status_code == 200
    assert put(env, headers("clerk"), "JAL", tmp_path).status_code == 200
    assert env.client.delete("/forms/CHU/2025Q1", headers=headers("clerk")).status_code == 204


def test_me_and_the_session_say_which_offices_a_user_has(env: Env) -> None:
    assert env.client.get("/me", headers=bearer(TWO_TOKEN)).json()["regions"] == ["NAR", "TAL"]
    assert env.call("clerk", "GET", "/me").json()["regions"] == []


def test_a_problem_of_another_office_is_read_but_not_changed(env: Env, tmp_path: Path) -> None:
    assert put(env, headers("admin"), "BAT", tmp_path, BAD).status_code == 200
    found = env.client.get("/problems?region=BAT", headers=bearer(OSH_TOKEN)).json()["problems"]
    assert len(found) == 1
    pid = found[0]["id"]
    said = env.client.post(
        f"/problems/{pid}/comments", json={"text": "not mine"}, headers=bearer(OSH_TOKEN)
    )
    refused(said, "BAT")
    given = env.client.post(
        f"/problems/{pid}/assignee", json={"assignee": "aibek"}, headers=bearer(OSH_TOKEN)
    )
    refused(given, "BAT")
    assert (
        env.call("clerk", "POST", f"/problems/{pid}/comments", json={"text": "seen"}).status_code
        == 201
    )


def test_a_region_must_have_the_form_of_a_code_and_only_an_operator_is_bound(
    tmp_path: Path,
) -> None:
    users = UserService(open_database(str(tmp_path / "u.db")))
    with pytest.raises(ValueError, match="region"):
        users.add("x1", "operator", set(), "a" * 64, T, regions={"osh oblast"})
    with pytest.raises(ValueError, match="operator"):
        users.add("x2", "designer", set(), "b" * 64, T, regions={"OSH"})
    made = users.add("x3", "operator", set(), "c" * 64, T, regions={"OSH"})
    assert made.regions == {"OSH"} and users.authenticate("c" * 64).regions == {"OSH"}  # type: ignore[union-attr]
    assert [u.regions for u in users.all()] == [frozenset({"OSH"})]


def test_a_database_made_before_this_story_gets_the_column_and_its_users_have_no_office(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "old.db")
    db = open_database(path)
    UserService(db).add("olga", "operator", set(), "d" * 64, T)
    db.commit()
    db.execute("ALTER TABLE users DROP COLUMN regions")
    db.commit()
    db.close()
    again = open_database(path)
    assert "regions" in {row[1] for row in again.execute("PRAGMA table_info(users)")}
    found = UserService(again).authenticate("d" * 64)
    assert found is not None and found.regions == frozenset()
    assert isinstance(again, sqlite3.Connection)
