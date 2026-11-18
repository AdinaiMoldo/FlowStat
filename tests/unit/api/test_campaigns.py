"""US-53: reporting campaigns through the API (api-spec 12)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from flowstat.model import campaign as life
from flowstat.repo.campaigns import CampaignRepo
from flowstat.repo.db import connect
from tests.unit.actions.helpers import GOOD_ROWS, REGION_CODES, write_all_forms, write_form
from tests.unit.api.helpers import EXAMPLE, NAME, Env, headers, make_env

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return make_env(tmp_path)


def body(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "Quarterly Regional Statistics, Q3 2026",
        "year": 2026,
        "quarter": 3,
        "regions": list(REGION_CODES),
        "deadline": "2026-10-31",
        "workflow": NAME,
        "workflow_version": 1,
    }
    base.update(changes)
    return base


def publish(env: Env) -> None:
    assert (
        env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    ).status_code == 201
    assert env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish").status_code == 200


def create(env: Env, **changes: Any) -> dict[str, Any]:
    reply = env.call("admin", "POST", "/campaigns", json=body(**changes))
    assert reply.status_code == 201, reply.text
    made: dict[str, Any] = reply.json()
    return made


def draft_only(env: Env) -> None:
    """A draft version of the workflow: it exists but is not published."""
    assert (
        env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    ).status_code == 201


def move(env: Env, campaign_id: str, to: str, role: str = "admin"):  # type: ignore[no-untyped-def]
    return env.call(role, "POST", f"/campaigns/{campaign_id}/transitions", json={"to": to})


def test_an_administrator_creates_a_campaign_and_it_starts_as_a_draft(env: Env) -> None:
    draft_only(env)
    made = create(env)
    assert made["status"] == "DRAFT" and made["period"] == "2026Q3"
    assert made["created_by"] == "ada" and made["regions"] == list(REGION_CODES)
    assert (made["preliminary_min"], made["final_min"]) == (7, 9)  # the defaults
    assert made["next"] == ["OPEN", "ARCHIVED"]
    assert made["progress"]["expected"] == 9 and made["progress"]["received"] == 0


def test_only_an_administrator_creates_or_changes_a_campaign(env: Env) -> None:
    draft_only(env)
    for role in ("designer", "operator", "clerk"):
        assert env.call(role, "POST", "/campaigns", json=body()).status_code == 403
    assert env.client.post("/campaigns", json=body()).status_code == 401
    made = create(env)
    for role in ("designer", "operator", "clerk"):
        assert env.call(role, "PUT", f"/campaigns/{made['id']}", json=body()).status_code == 403
        assert move(env, made["id"], "OPEN", role).status_code == 403


def test_every_role_can_read_the_campaigns(env: Env) -> None:
    draft_only(env)
    made = create(env)
    for role in ("admin", "designer", "operator", "clerk"):
        listed = env.call(role, "GET", "/campaigns")
        assert listed.status_code == 200 and [c["id"] for c in listed.json()] == [made["id"]]
        assert env.call(role, "GET", f"/campaigns/{made['id']}").status_code == 200


def test_an_unknown_campaign_is_404_for_every_route(env: Env) -> None:
    assert env.call("admin", "GET", "/campaigns/nothing").status_code == 404
    assert env.call("admin", "PUT", "/campaigns/nothing", json=body()).status_code == 404
    assert move(env, "nothing", "OPEN").status_code == 404


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"name": ""}, "name"),
        ({"regions": ["BIS", "NOWHERE"]}, "regions"),
        ({"deadline": "soon"}, "deadline"),
        ({"quarter": 7}, "quarter"),
        ({"final_min": 12}, "final_min"),
        ({"workflow": "nothing"}, "workflow"),
        ({"workflow_version": 9}, "workflow"),
    ],
)
def test_wrong_settings_are_refused_with_the_field(
    env: Env, changes: dict[str, Any], field: str
) -> None:
    draft_only(env)
    reply = env.call("admin", "POST", "/campaigns", json=body(**changes))
    assert reply.status_code == 422, reply.text
    error = reply.json()["errors"][0]
    assert error["code"] == "E-CAMP-001" and error["field"] == field


def test_an_unknown_field_is_refused_and_so_is_a_missing_one(env: Env) -> None:
    draft_only(env)
    assert (
        env.call("admin", "POST", "/campaigns", json={**body(), "status": "APPROVED"}).status_code
        == 422
    )
    incomplete = body()
    del incomplete["deadline"]
    assert env.call("admin", "POST", "/campaigns", json=incomplete).status_code == 422


def test_a_name_is_used_once(env: Env) -> None:
    draft_only(env)
    create(env)
    again = env.call("admin", "POST", "/campaigns", json=body())
    assert again.status_code == 409 and again.json()["errors"][0]["code"] == "E-CAMP-005"


def test_a_draft_can_be_edited_and_a_campaign_that_is_open_cannot(env: Env) -> None:
    publish(env)
    made = create(env)
    edited = env.call("admin", "PUT", f"/campaigns/{made['id']}", json=body(deadline="2026-11-15"))
    assert edited.status_code == 200 and edited.json()["deadline"] == "2026-11-15"
    assert move(env, made["id"], "OPEN").status_code == 200
    late = env.call("admin", "PUT", f"/campaigns/{made['id']}", json=body(deadline="2026-12-01"))
    assert late.status_code == 409 and late.json()["errors"][0]["code"] == "E-CAMP-004"


def test_renaming_a_draft_to_the_name_of_another_is_refused(env: Env) -> None:
    draft_only(env)
    create(env, name="one")
    second = create(env, name="two", quarter=2)
    clash = env.call("admin", "PUT", f"/campaigns/{second['id']}", json=body(name="one"))
    assert clash.status_code == 409 and clash.json()["errors"][0]["code"] == "E-CAMP-005"


def test_a_campaign_cannot_open_until_its_workflow_version_is_published(env: Env) -> None:
    draft_only(env)
    made = create(env)
    reply = move(env, made["id"], "OPEN")
    assert reply.status_code == 409 and reply.json()["errors"][0]["code"] == "E-CAMP-008"
    assert env.call("admin", "GET", f"/campaigns/{made['id']}").json()["status"] == "DRAFT"
    assert env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish").status_code == 200
    assert move(env, made["id"], "OPEN").json()["status"] == "OPEN"


def test_a_person_walks_the_collecting_part_of_the_life_cycle(env: Env) -> None:
    publish(env)
    made = create(env)
    walked = ["DRAFT"]
    for to in ("OPEN", "COLLECTING", "VALIDATING", "COLLECTING", "VALIDATING"):
        reply = move(env, made["id"], to)
        assert reply.status_code == 200, (to, reply.text)
        walked.append(reply.json()["status"])
    assert walked == ["DRAFT", "OPEN", "COLLECTING", "VALIDATING", "COLLECTING", "VALIDATING"]
    assert reply.json()["next"] == ["COLLECTING"]


def test_a_person_cannot_make_the_changes_that_belong_to_the_system(env: Env) -> None:
    publish(env)
    made = create(env)
    for to in ("OPEN", "COLLECTING", "VALIDATING"):
        move(env, made["id"], to)
    for to in ("READY_FOR_CONSOLIDATION", "PRELIMINARY", "PENDING_APPROVAL", "APPROVED"):
        reply = move(env, made["id"], to)
        assert reply.status_code == 409, to
        assert reply.json()["errors"][0]["code"] in ("E-CAMP-002", "E-CAMP-003")
    assert (
        move(env, made["id"], "READY_FOR_CONSOLIDATION").json()["errors"][0]["code"] == "E-CAMP-003"
    )
    assert env.call("admin", "GET", f"/campaigns/{made['id']}").json()["status"] == "VALIDATING"


def test_a_cancelled_draft_is_archived_and_stays_there(env: Env) -> None:
    draft_only(env)
    made = create(env)
    assert move(env, made["id"], "ARCHIVED").json()["status"] == "ARCHIVED"
    assert move(env, made["id"], "OPEN").status_code == 409
    assert move(env, made["id"], "NONSENSE").status_code == 409


def test_the_progress_counts_received_valid_invalid_and_missing_forms(
    env: Env, tmp_path: Path
) -> None:
    draft_only(env)
    made = create(env)
    write_all_forms(env.config.data_dir, "2026Q3", missing=("JAL", "ISK", "NAR"))
    write_form(env.config.data_dir / "OSH" / "2026Q3.xlsx", [("A", "a", -1)])  # a negative value
    write_all_forms(env.config.data_dir, "2025Q4")  # another quarter does not count
    got = env.call("operator", "GET", f"/campaigns/{made['id']}").json()["progress"]
    assert (got["expected"], got["received"], got["valid"], got["invalid"]) == (9, 6, 5, 1)
    assert got["missing"] == ["JAL", "ISK", "NAR"]
    assert got["preliminary_ready"] is False and got["final_ready"] is False
    osh = next(r for r in got["regions"] if r["region"] == "OSH")
    assert osh["received"] and not osh["valid"] and osh["problems"] == 1


def test_the_progress_says_when_enough_forms_have_come(env: Env) -> None:
    draft_only(env)
    made = create(env)
    write_all_forms(env.config.data_dir, "2026Q3", missing=("JAL", "ISK"))
    got = env.call("admin", "GET", f"/campaigns/{made['id']}").json()["progress"]
    assert got["preliminary_ready"] is True and got["final_ready"] is False  # 7 of 9
    write_all_forms(env.config.data_dir, "2026Q3")
    got = env.call("admin", "GET", f"/campaigns/{made['id']}").json()["progress"]
    assert got["final_ready"] is True


def test_only_the_regions_of_the_campaign_count(env: Env) -> None:
    draft_only(env)
    made = create(env, regions=["BIS", "OSC", "BAT"])
    write_all_forms(env.config.data_dir, "2026Q3")
    got = env.call("admin", "GET", f"/campaigns/{made['id']}").json()["progress"]
    assert (got["expected"], got["received"], got["valid"]) == (3, 3, 3)
    assert (made["preliminary_min"], made["final_min"]) == (3, 3)


def put_form(env: Env, role: str, region: str, quarter: str, tmp_path: Path):  # type: ignore[no-untyped-def]
    path = write_form(tmp_path / "made.xlsx", GOOD_ROWS)
    return env.client.put(
        f"/forms/{region}/{quarter}",
        content=path.read_bytes(),
        headers={**headers(role), "Content-Type": XLSX},
    )


def lock(env: Env, campaign_id: str, status: str) -> None:
    db = connect(env.config.db_path)
    try:
        CampaignRepo(db).db.execute(
            "UPDATE campaigns SET status = ? WHERE id = ?", (status, campaign_id)
        )
    finally:
        db.close()


@pytest.mark.parametrize("status", sorted(life.LOCKED))
def test_the_forms_of_an_approved_campaign_cannot_be_replaced_or_removed(
    env: Env, tmp_path: Path, status: str
) -> None:
    draft_only(env)
    made = create(env)
    write_all_forms(env.config.data_dir, "2026Q3")
    lock(env, made["id"], status)
    sent = put_form(env, "operator", "BIS", "2026Q3", tmp_path)
    assert sent.status_code == 409 and sent.json()["errors"][0]["code"] == "E-CAMP-006"
    gone = env.call("operator", "DELETE", "/forms/BIS/2026Q3")
    assert gone.status_code == 409 and gone.json()["errors"][0]["code"] == "E-CAMP-006"
    assert (env.config.data_dir / "BIS" / "2026Q3.xlsx").is_file()  # nothing was changed
    assert put_form(env, "operator", "BIS", "2026Q4", tmp_path).status_code == 200  # other period
    assert (
        env.call("operator", "GET", "/forms?quarter=2026Q3").status_code == 200
    )  # reading is free


@pytest.mark.parametrize("status", ["DRAFT", "OPEN", "COLLECTING", "VALIDATING", "REJECTED"])
def test_the_forms_of_a_campaign_that_is_still_collecting_can_change(
    env: Env, tmp_path: Path, status: str
) -> None:
    draft_only(env)
    made = create(env)
    lock(env, made["id"], status)
    assert put_form(env, "operator", "BIS", "2026Q3", tmp_path).status_code == 200
    assert env.call("operator", "DELETE", "/forms/BIS/2026Q3").status_code == 204


def test_a_period_with_no_campaign_is_as_free_as_before(env: Env, tmp_path: Path) -> None:
    assert put_form(env, "operator", "BIS", "2025Q1", tmp_path).status_code == 200
