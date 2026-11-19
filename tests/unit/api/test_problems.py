"""US-56: an error of a form is a problem with an assignee, comments and a history; a corrected
form moves it to revalidating and then to resolved or reopened."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.unit.actions.helpers import FIVE_ROWS, Row, write_form
from tests.unit.api.helpers import Env, T, headers, make_env

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
BAD: list[Row] = [*FIVE_ROWS[:3], ("I04", "Deaths", -2.5), FIVE_ROWS[4]]  # deaths below zero
TWO: list[Row] = [
    *FIVE_ROWS[:1],
    ("I02", "Households", 999),
    *FIVE_ROWS[2:3],
    ("I04", "Deaths", -2.5),
    FIVE_ROWS[4],
]


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return make_env(tmp_path)


def put(
    env: Env, tmp_path: Path, rows: list[Row], role: str = "operator", region: str = "BAT"
) -> Any:
    content = write_form(tmp_path / "made.xlsx", rows).read_bytes()
    return env.client.put(
        f"/forms/{region}/2025Q1", content=content, headers={**headers(role), "Content-Type": XLSX}
    )


def campaign(env: Env) -> None:
    from tests.unit.api.helpers import EXAMPLE, NAME

    assert (
        env.call(
            "designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE}
        ).status_code
        == 201
    )
    made = env.call("admin", "POST", "/campaigns", json={
        "name": "Q1 2025", "year": 2025, "quarter": 1, "regions": ["BIS", "OSC", "BAT"],
        "deadline": "2025-04-30", "workflow": NAME, "workflow_version": 1,
        "rules": "regional-indicators-v1",
    })  # fmt: skip
    assert made.status_code == 201, made.text


def problems(env: Env, query: str = "", role: str = "operator") -> list[dict[str, Any]]:
    reply = env.call(role, "GET", f"/problems{query}")
    assert reply.status_code == 200, reply.text
    return list(reply.json()["problems"])


# -- a form with an error opens a problem -----------------------------------------------------
def test_each_error_of_a_form_opens_a_problem_assigned_to_who_sent_it(
    env: Env, tmp_path: Path
) -> None:
    campaign(env)
    put(env, tmp_path, TWO)
    found = problems(env)
    assert sorted((p["code"], p["indicator"], p["status"], p["assignee"]) for p in found) == [
        ("VAL-RANGE-001", "I04", "OPEN", "olga"),
        ("VAL-REL-001", "I02", "OPEN", "olga"),
    ]
    one = next(p for p in found if p["indicator"] == "I04")
    assert (one["region"], one["quarter"], one["cell"], one["opened_in"], one["seen_in"]) == (
        "BAT",
        "2025Q1",
        "C5",
        1,
        1,
    )
    assert (one["actual"], one["expected"]) == ("-2.5", "0 or more")


def test_a_good_form_and_a_warning_open_nothing(env: Env, tmp_path: Path) -> None:
    campaign(env)
    put(env, tmp_path, FIVE_ROWS)
    put(env, tmp_path, [*FIVE_ROWS, ("X9", "Other", 1)], "admin", "OSC")  # a warning only
    assert problems(env) == []


def test_the_history_starts_with_the_opening(env: Env, tmp_path: Path) -> None:
    put(env, tmp_path, BAD)
    (p,) = problems(env)
    detail = env.call("operator", "GET", f"/problems/{p['id']}").json()
    assert [(e["kind"], e["by"], e["version"], e["at"]) for e in detail["events"]] == [
        ("OPENED", "system", 1, T)
    ]
    assert "negative" in detail["events"][0]["text"]


# -- a corrected form -------------------------------------------------------------------------
def test_a_corrected_form_resolves_the_problem_through_revalidating(
    env: Env, tmp_path: Path
) -> None:
    put(env, tmp_path, BAD)
    put(env, tmp_path, FIVE_ROWS, "admin")
    (p,) = problems(env)
    assert (p["status"], p["seen_in"], p["opened_in"]) == ("RESOLVED", 1, 1)
    detail = env.call("operator", "GET", f"/problems/{p['id']}").json()
    assert [(e["kind"], e["version"]) for e in detail["events"]] == [
        ("OPENED", 1),
        ("REVALIDATING", 2),
        ("RESOLVED", 2),
    ]


def test_a_form_that_is_still_wrong_reopens_the_problem_and_follows_the_error(
    env: Env, tmp_path: Path
) -> None:
    put(env, tmp_path, BAD)
    moved: list[Row] = [
        FIVE_ROWS[0],
        FIVE_ROWS[1],
        ("I04", "Deaths", -9),
        *FIVE_ROWS[2:3],
        FIVE_ROWS[4],
    ]
    put(env, tmp_path, moved)
    (p,) = problems(env)
    assert (p["status"], p["seen_in"], p["cell"], p["actual"]) == ("REOPENED", 2, "C4", "-9")
    detail = env.call("operator", "GET", f"/problems/{p['id']}").json()
    assert [e["kind"] for e in detail["events"]] == ["OPENED", "REVALIDATING", "REOPENED"]


def test_a_resolved_problem_comes_back_when_the_error_does(env: Env, tmp_path: Path) -> None:
    put(env, tmp_path, BAD)
    put(env, tmp_path, FIVE_ROWS)
    put(env, tmp_path, BAD)
    (p,) = problems(env)
    assert (
        p["status"] == "REOPENED" and p["seen_in"] == 3 and len(problems(env, "?active=true")) == 1
    )
    kinds = [
        e["kind"] for e in env.call("operator", "GET", f"/problems/{p['id']}").json()["events"]
    ]
    assert kinds == ["OPENED", "REVALIDATING", "RESOLVED", "REOPENED"]


def test_the_same_file_sent_again_changes_no_problem(env: Env, tmp_path: Path) -> None:
    put(env, tmp_path, BAD)
    content = (env.config.data_dir / "BAT" / "2025Q1.xlsx").read_bytes()
    env.client.put(
        "/forms/BAT/2025Q1", content=content, headers={**headers("operator"), "Content-Type": XLSX}
    )
    (p,) = problems(env)
    assert p["status"] == "OPEN"
    assert len(env.call("operator", "GET", f"/problems/{p['id']}").json()["events"]) == 1


# -- people at work ---------------------------------------------------------------------------
def test_a_problem_is_assigned_to_another_user_and_the_change_is_in_the_history(
    env: Env, tmp_path: Path
) -> None:
    put(env, tmp_path, BAD)
    (p,) = problems(env)
    reply = env.call("admin", "POST", f"/problems/{p['id']}/assignee", json={"assignee": "kira"})
    assert reply.status_code == 200 and reply.json()["assignee"] == "kira"
    last = reply.json()["events"][-1]
    assert (last["kind"], last["by"], last["text"]) == ("ASSIGNED", "ada", "kira")
    reply = env.call("operator", "POST", f"/problems/{p['id']}/assignee", json={"assignee": None})
    assert reply.json()["assignee"] is None and reply.json()["events"][-1]["by"] == "olga"


def test_the_assignee_must_be_a_user_that_exists(env: Env, tmp_path: Path) -> None:
    put(env, tmp_path, BAD)
    (p,) = problems(env)
    reply = env.call("admin", "POST", f"/problems/{p['id']}/assignee", json={"assignee": "nobody"})
    error = reply.json()["errors"][0]
    assert (
        reply.status_code == 422 and error["code"] == "E-PROB-002" and error["field"] == "assignee"
    )


def test_a_comment_is_added_to_the_history_with_its_author(env: Env, tmp_path: Path) -> None:
    put(env, tmp_path, BAD)
    (p,) = problems(env)
    reply = env.call(
        "operator",
        "POST",
        f"/problems/{p['id']}/comments",
        json={"text": "  checking with the office  "},
    )
    assert reply.status_code == 201
    last = reply.json()["events"][-1]
    assert (last["kind"], last["by"], last["text"]) == (
        "COMMENTED",
        "olga",
        "checking with the office",
    )
    assert reply.json()["status"] == "OPEN"  # a comment changes nothing else


@pytest.mark.parametrize("text", ["", "   ", "x" * 2001])
def test_a_comment_has_text(env: Env, tmp_path: Path, text: str) -> None:
    put(env, tmp_path, BAD)
    (p,) = problems(env)
    reply = env.call("operator", "POST", f"/problems/{p['id']}/comments", json={"text": text})
    assert reply.status_code == 422 and reply.json()["errors"][0]["code"] == "E-PROB-001"


def test_a_resolved_problem_cannot_be_assigned_but_can_be_commented(
    env: Env, tmp_path: Path
) -> None:
    put(env, tmp_path, BAD)
    put(env, tmp_path, FIVE_ROWS)
    (p,) = problems(env)
    refused = env.call("admin", "POST", f"/problems/{p['id']}/assignee", json={"assignee": "kira"})
    assert refused.status_code == 409 and refused.json()["errors"][0]["code"] == "E-PROB-003"
    assert (
        env.call(
            "admin", "POST", f"/problems/{p['id']}/comments", json={"text": "thanks"}
        ).status_code
        == 201
    )


# -- the listing ------------------------------------------------------------------------------
def test_the_listing_filters(env: Env, tmp_path: Path) -> None:
    put(env, tmp_path, BAD)
    put(env, tmp_path, BAD, "admin", "OSC")
    put(env, tmp_path, FIVE_ROWS, "admin", "OSC")  # resolved
    assert len(problems(env)) == 2
    assert [p["region"] for p in problems(env, "?active=true")] == ["BAT"]
    assert [p["region"] for p in problems(env, "?status=RESOLVED")] == ["OSC"]
    assert [p["region"] for p in problems(env, "?region=BAT&quarter=2025Q1")] == ["BAT"]
    assert problems(env, "?quarter=2024Q4") == []
    assert [p["region"] for p in problems(env, "?assignee=olga&active=true")] == ["BAT"]


# -- who may ----------------------------------------------------------------------------------
def test_every_role_reads_and_a_designer_cannot_write(env: Env, tmp_path: Path) -> None:
    put(env, tmp_path, BAD)
    (p,) = problems(env, role="designer")
    assert env.call("designer", "GET", f"/problems/{p['id']}").status_code == 200
    assert (
        env.call(
            "designer", "POST", f"/problems/{p['id']}/comments", json={"text": "x"}
        ).status_code
        == 403
    )
    assert (
        env.call(
            "designer", "POST", f"/problems/{p['id']}/assignee", json={"assignee": None}
        ).status_code
        == 403
    )
    assert env.call(None, "GET", "/problems").status_code == 401


def test_an_unknown_problem_is_not_found(env: Env) -> None:
    assert env.call("operator", "GET", "/problems/nope").status_code == 404
    assert (
        env.call("operator", "POST", "/problems/nope/comments", json={"text": "x"}).status_code
        == 404
    )
    assert (
        env.call("operator", "POST", "/problems/nope/assignee", json={"assignee": None}).status_code
        == 404
    )
