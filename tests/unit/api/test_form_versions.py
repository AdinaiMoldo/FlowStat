"""US-55: a region that sends its form again gets a new version; both are kept with their
results, the current one is the latest, and what the second fixed can be asked for."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.unit.actions.helpers import FIVE_ROWS, REGION_CODES, Row, write_form
from tests.unit.api.helpers import EXAMPLE, NAME, Env, T, headers, make_env

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
BAD: list[Row] = [*FIVE_ROWS[:3], ("I04", "Deaths", -2.5), FIVE_ROWS[4]]  # deaths below zero
WORSE: list[Row] = [*FIVE_ROWS[:3], ("I04", "Deaths", -2.5), ("I05", "Marriages", 999)]


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return make_env(tmp_path)


def put(env: Env, tmp_path: Path, rows: list[Row], region: str = "BAT", quarter: str = "2025Q1",
        role: str = "operator") -> Any:  # fmt: skip
    content = write_form(tmp_path / "made.xlsx", rows).read_bytes()
    return env.client.put(
        f"/forms/{region}/{quarter}",
        content=content,
        headers={**headers(role), "Content-Type": XLSX},
    )


def campaign(env: Env, **changes: Any) -> None:
    assert (
        env.call(
            "designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE}
        ).status_code
        == 201
    )
    base: dict[str, Any] = {
        "name": "Q1 2025", "year": 2025, "quarter": 1, "regions": list(REGION_CODES),
        "deadline": "2025-04-30", "workflow": NAME, "workflow_version": 1,
        "rules": "regional-indicators-v1",
    }  # fmt: skip
    assert env.call("admin", "POST", "/campaigns", json={**base, **changes}).status_code == 201


def versions(env: Env, region: str = "BAT", quarter: str = "2025Q1") -> list[dict[str, Any]]:
    reply = env.call("operator", "GET", f"/forms/{region}/{quarter}/versions")
    assert reply.status_code == 200, reply.text
    return list(reply.json()["versions"])


# -- sending twice -------------------------------------------------------------------------------
def test_the_first_form_is_version_one_and_says_who_sent_it_and_when(
    env: Env, tmp_path: Path
) -> None:
    body = put(env, tmp_path, FIVE_ROWS).json()
    assert (body["version"], body["versions"]) == (1, 1)
    assert (body["uploaded_by"], body["uploaded_at"]) == ("olga", T)


def test_a_second_form_is_version_two_and_the_first_is_kept(env: Env, tmp_path: Path) -> None:
    campaign(env)
    put(env, tmp_path, BAD)
    reply = put(env, tmp_path, FIVE_ROWS, role="admin")
    assert (reply.json()["version"], reply.json()["versions"]) == (2, 2)
    assert reply.json()["uploaded_by"] == "ada"
    listed = versions(env)
    assert [(v["version"], v["current"], v["errors"], v["uploaded_by"]) for v in listed] == [
        (2, True, 0, "ada"),
        (1, False, 1, "olga"),
    ]
    first = env.call("operator", "GET", "/forms/BAT/2025Q1/versions/1").json()
    assert [f["code"] for f in first["findings"]] == ["VAL-RANGE-001"]  # kept, not recomputed
    assert env.call("operator", "GET", "/forms/BAT/2025Q1/versions/2").json()["findings"] == []


def test_the_file_that_a_run_reads_is_the_current_version(env: Env, tmp_path: Path) -> None:
    put(env, tmp_path, BAD)
    put(env, tmp_path, FIVE_ROWS)
    from openpyxl import load_workbook

    sheet = load_workbook(env.config.data_dir / "BAT" / "2025Q1.xlsx").worksheets[0]
    assert [row[2] for row in sheet.iter_rows(min_row=2, values_only=True)][3] == 2.5


def test_the_same_file_sent_again_is_not_a_new_version(env: Env, tmp_path: Path) -> None:
    put(env, tmp_path, BAD)
    # an identical workbook made twice differs by its time stamp: send the very same bytes
    content = (env.config.data_dir / "BAT" / "2025Q1.xlsx").read_bytes()
    reply = env.client.put("/forms/BAT/2025Q1", content=content,
                           headers={**headers("operator"), "Content-Type": XLSX})  # fmt: skip
    assert reply.status_code == 200 and (reply.json()["version"], reply.json()["versions"]) == (
        1,
        1,
    )
    assert len(versions(env)) == 1


def test_the_listing_says_which_version_each_region_is_at(env: Env, tmp_path: Path) -> None:
    put(env, tmp_path, FIVE_ROWS, "BIS")
    put(env, tmp_path, BAD, "BAT")
    put(env, tmp_path, FIVE_ROWS, "BAT")
    listed = {
        f["region"]: f for f in env.call("designer", "GET", "/forms?quarter=2025Q1").json()["forms"]
    }
    assert (listed["BIS"]["version"], listed["BIS"]["versions"]) == (1, 1)
    assert (listed["BAT"]["version"], listed["BAT"]["versions"]) == (2, 2)
    assert (listed["OSC"]["version"], listed["OSC"]["versions"]) == (None, 0)  # nothing sent


# -- what the second fixed ----------------------------------------------------------------------
def test_the_comparison_tells_what_is_fixed_what_remains_and_what_is_new(
    env: Env, tmp_path: Path
) -> None:
    campaign(env)
    put(env, tmp_path, BAD)
    put(env, tmp_path, WORSE)  # the deaths are still negative, and marriages are more than people
    got = env.call("operator", "GET", "/forms/BAT/2025Q1/compare?older=1&newer=2").json()
    assert (got["older"], got["newer"]) == (1, 2)
    assert [f["code"] for f in got["remaining"]] == ["VAL-RANGE-001"]
    assert [(f["code"], f["indicator"]) for f in got["introduced"]] == [("VAL-REL-001", "I05")]
    assert got["fixed"] == []
    put(env, tmp_path, FIVE_ROWS)
    fixed = env.call(
        "operator", "GET", "/forms/BAT/2025Q1/compare"
    ).json()  # latest against the one before
    assert (fixed["older"], fixed["newer"]) == (2, 3)
    assert sorted(f["code"] for f in fixed["fixed"]) == ["VAL-RANGE-001", "VAL-REL-001"]
    assert fixed["remaining"] == [] and fixed["introduced"] == []


def test_there_is_nothing_to_compare_with_a_first_version(env: Env, tmp_path: Path) -> None:
    put(env, tmp_path, FIVE_ROWS)
    reply = env.call("operator", "GET", "/forms/BAT/2025Q1/compare")
    assert reply.status_code == 404 and "earlier" in reply.json()["errors"][0]["message"]


# -- removing and sending again ----------------------------------------------------------------
def test_removing_a_form_keeps_its_history_and_a_new_form_continues_it(
    env: Env, tmp_path: Path
) -> None:
    put(env, tmp_path, BAD)
    put(env, tmp_path, FIVE_ROWS)
    assert env.call("operator", "DELETE", "/forms/BAT/2025Q1").status_code == 204
    assert not (env.config.data_dir / "BAT" / "2025Q1.xlsx").exists()
    kept = versions(env)
    assert [(v["version"], v["current"]) for v in kept] == [(2, False), (1, False)]
    assert (kept[0]["removed_by"], kept[0]["removed_at"]) == ("olga", T)
    listed = next(
        f
        for f in env.call("operator", "GET", "/forms?quarter=2025Q1").json()["forms"]
        if f["region"] == "BAT"
    )
    assert (listed["received"], listed["version"], listed["versions"]) == (False, None, 2)
    again = put(env, tmp_path, BAD).json()
    assert (again["version"], again["versions"]) == (3, 3)


# -- what does not make a version -----------------------------------------------------------------
def test_a_file_that_is_not_a_form_is_refused_and_leaves_no_version(
    env: Env, tmp_path: Path
) -> None:
    put(env, tmp_path, FIVE_ROWS)
    bad = env.client.put("/forms/BAT/2025Q1", content=b"text",
                         headers={**headers("operator"), "Content-Type": XLSX})  # fmt: skip
    assert bad.status_code == 422
    assert len(versions(env)) == 1


def test_a_form_of_a_locked_quarter_is_refused_and_leaves_no_version(
    env: Env, tmp_path: Path
) -> None:
    from flowstat.repo.campaigns import CampaignRepo
    from flowstat.repo.db import connect

    campaign(env)
    put(env, tmp_path, FIVE_ROWS)
    db = connect(env.config.db_path)
    campaigns = CampaignRepo(db)
    (made,) = campaigns.all()
    db.execute("UPDATE campaigns SET status = 'APPROVED' WHERE id = ?", (made.id,))
    db.close()
    assert put(env, tmp_path, BAD).status_code == 409
    assert len(versions(env)) == 1


def test_a_form_that_was_put_there_by_hand_has_no_version(env: Env) -> None:
    write_form(env.config.data_dir / "BAT" / "2025Q1.xlsx", FIVE_ROWS)
    listed = next(
        f
        for f in env.call("operator", "GET", "/forms?quarter=2025Q1").json()["forms"]
        if f["region"] == "BAT"
    )
    assert listed["received"] and (listed["version"], listed["versions"]) == (None, 0)
    assert versions(env) == []


def test_a_file_changed_after_it_was_sent_is_no_longer_the_version(
    env: Env, tmp_path: Path
) -> None:
    put(env, tmp_path, FIVE_ROWS)
    write_form(env.config.data_dir / "BAT" / "2025Q1.xlsx", BAD)  # someone replaced it on the disk
    listed = next(
        f
        for f in env.call("operator", "GET", "/forms?quarter=2025Q1").json()["forms"]
        if f["region"] == "BAT"
    )
    assert listed["version"] is None and listed["versions"] == 1


# -- who may ask, and what is not there ------------------------------------------------------------
def test_every_role_may_read_the_history_and_nobody_without_a_token(
    env: Env, tmp_path: Path
) -> None:
    put(env, tmp_path, FIVE_ROWS)
    for role in ("admin", "designer", "operator"):
        assert env.call(role, "GET", "/forms/BAT/2025Q1/versions").status_code == 200
    assert env.call(None, "GET", "/forms/BAT/2025Q1/versions").status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/forms/XXX/2025Q1/versions",
        "/forms/BAT/2025Q9/versions",
        "/forms/BAT/2025Q1/versions/7",
        "/forms/BAT/2025Q1/versions/0",
        "/forms/BAT/2025Q1/compare?older=1&newer=9",
    ],
)
def test_what_is_not_there_is_not_found_or_refused(env: Env, tmp_path: Path, path: str) -> None:
    put(env, tmp_path, FIVE_ROWS)
    assert env.call("operator", "GET", path).status_code in (404, 422)
