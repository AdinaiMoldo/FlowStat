"""US-57: the preliminary and the final result of a campaign through the API (api-spec 14)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.unit.actions.helpers import FIVE_ROWS, Row, write_form
from tests.unit.api.helpers import Env, headers, make_env
from tests.unit.api.test_campaigns import create, move, publish

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
BAD: list[Row] = [*FIVE_ROWS[:3], ("I04", "Deaths", -2.5), FIVE_ROWS[4]]  # deaths below zero
MORE: list[Row] = [("I01", "Population", 400.1), *FIVE_ROWS[1:]]
REGIONS = ["BIS", "OSC", "BAT", "JAL"]


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return make_env(tmp_path)


def send(env: Env, tmp_path: Path, region: str, rows: list[Row] = FIVE_ROWS) -> None:
    content = write_form(tmp_path / "made.xlsx", rows).read_bytes()
    reply = env.client.put(
        f"/forms/{region}/2026Q3",
        content=content,
        headers={**headers("admin"), "Content-Type": XLSX},
    )
    assert reply.status_code == 200, reply.text


def campaign(env: Env, to: str = "VALIDATING", **changes: Any) -> str:
    publish(env)
    settings = {"regions": REGIONS, "rules": "regional-indicators-v1", "preliminary_min": 2,
                "final_min": 4, **changes}  # fmt: skip
    cid = str(create(env, **settings)["id"])
    for step in ("OPEN", "COLLECTING", "VALIDATING"):
        assert move(env, cid, step).status_code == 200
        if step == to:
            break
    return cid


def make(env: Env, cid: str, kind: str, role: str | None = "admin"):  # type: ignore[no-untyped-def]
    return env.call(role, "POST", f"/campaigns/{cid}/consolidations", json={"kind": kind})


def status(env: Env, cid: str) -> str:
    return str(env.call("admin", "GET", f"/campaigns/{cid}").json()["status"])


def test_a_preliminary_result_says_who_is_in_who_is_out_and_why(env: Env, tmp_path: Path) -> None:
    send(env, tmp_path, "BIS")
    send(env, tmp_path, "OSC")
    send(env, tmp_path, "OSC", MORE)  # a second version: the result reads the current one
    send(env, tmp_path, "BAT", BAD)
    cid = campaign(env)
    reply = make(env, cid, "PRELIMINARY")
    assert reply.status_code == 201, reply.text
    made = reply.json()
    assert (made["number"], made["kind"], made["complete"], made["stale"]) == (
        1,
        "PRELIMINARY",
        False,
        False,
    )
    assert made["made_by"] == "ada" and made["period"] == "2026Q3"
    assert [(i["region"], i["version"]) for i in made["included"]] == [("BIS", 1), ("OSC", 2)]
    assert all(len(i["sha256"]) == 64 for i in made["included"])
    assert [(o["region"], o["reason"]) for o in made["left_out"]] == [
        ("BAT", "invalid"), ("JAL", "missing"),
    ]  # fmt: skip
    assert "1 error" in made["left_out"][0]["detail"]
    assert (made["count"], made["expected"], made["share"]) == (2, 4, "50.0")
    assert status(env, cid) == "PRELIMINARY"


def test_a_total_is_exact_and_can_be_traced_to_the_forms(env: Env, tmp_path: Path) -> None:
    send(env, tmp_path, "BIS")
    send(env, tmp_path, "OSC", MORE)
    cid = campaign(env)
    assert make(env, cid, "PRELIMINARY").status_code == 201
    read = env.call("designer", "GET", f"/campaigns/{cid}/consolidations/1")
    assert read.status_code == 200
    first = read.json()["lines"][0]
    assert (first["code"], first["name"], first["total"]) == ("I01", "Population", "800.1")
    assert first["parts"] == [
        {"region": "BIS", "version": 1, "value": "400"},
        {"region": "OSC", "version": 1, "value": "400.1"},
    ]
    assert [line["code"] for line in read.json()["lines"]] == ["I01", "I02", "I03", "I04", "I05"]


def test_too_few_valid_forms_refuse_the_result_and_the_campaign_stays(
    env: Env, tmp_path: Path
) -> None:
    send(env, tmp_path, "BIS")
    cid = campaign(env)
    reply = make(env, cid, "PRELIMINARY")
    assert reply.status_code == 409 and reply.json()["errors"][0]["code"] == "E-CONS-001"
    assert status(env, cid) == "VALIDATING"
    assert env.call("admin", "GET", f"/campaigns/{cid}/consolidations").json()["results"] == []


def test_a_final_result_needs_the_policy_and_then_waits_for_the_approval(
    env: Env, tmp_path: Path
) -> None:
    for region in REGIONS[:3]:
        send(env, tmp_path, region)
    cid = campaign(env)
    refused = make(env, cid, "FINAL")
    assert refused.status_code == 409 and refused.json()["errors"][0]["code"] == "E-CONS-002"
    assert "JAL" in refused.json()["errors"][0]["message"]
    assert make(env, cid, "PRELIMINARY").status_code == 201
    send(env, tmp_path, "JAL")  # the form that was missing: the earlier result is now stale
    assert status(env, cid) == "COLLECTING"
    assert move(env, cid, "VALIDATING").status_code == 200
    final = make(env, cid, "FINAL")
    assert final.status_code == 201, final.text
    assert (final.json()["number"], final.json()["kind"], final.json()["complete"]) == (
        2,
        "FINAL",
        True,
    )
    assert status(env, cid) == "PENDING_APPROVAL"


def test_a_corrected_form_marks_the_result_stale_and_the_next_one_is_a_new_version(
    env: Env, tmp_path: Path
) -> None:
    send(env, tmp_path, "BIS")
    send(env, tmp_path, "OSC")
    cid = campaign(env)
    assert make(env, cid, "PRELIMINARY").status_code == 201
    send(env, tmp_path, "OSC", MORE)
    listed = env.call("operator", "GET", f"/campaigns/{cid}/consolidations").json()["results"]
    assert [(r["number"], r["stale"]) for r in listed] == [(1, True)]
    assert "OSC" in listed[0]["stale_reason"] and listed[0]["stale_at"]
    assert move(env, cid, "VALIDATING").status_code == 200
    again = make(env, cid, "PRELIMINARY").json()
    assert (again["number"], again["stale"]) == (2, False)
    assert again["lines"][0]["total"] == "800.1"
    listed = env.call("operator", "GET", f"/campaigns/{cid}/consolidations").json()["results"]
    assert [(r["number"], r["stale"]) for r in listed] == [(2, False), (1, True)]
    old = env.call("admin", "GET", f"/campaigns/{cid}/consolidations/1").json()
    assert old["lines"][0]["total"] == "800" and old["stale"] is True  # what it was is kept


def test_a_removed_form_and_a_form_of_a_region_outside_the_campaign(
    env: Env, tmp_path: Path
) -> None:
    send(env, tmp_path, "BIS")
    send(env, tmp_path, "OSC")
    cid = campaign(env)
    assert make(env, cid, "PRELIMINARY").status_code == 201
    send(env, tmp_path, "CHU")  # not a region of this campaign: nothing changes
    assert status(env, cid) == "PRELIMINARY"
    gone = env.client.delete("/forms/OSC/2026Q3", headers=headers("admin"))
    assert gone.status_code == 204
    result = env.call("admin", "GET", f"/campaigns/{cid}/consolidations/1").json()
    assert result["stale"] is True and "removed" in result["stale_reason"]
    assert status(env, cid) == "COLLECTING"


def test_a_result_is_made_only_while_the_campaign_is_being_validated(
    env: Env, tmp_path: Path
) -> None:
    send(env, tmp_path, "BIS")
    send(env, tmp_path, "OSC")
    cid = campaign(env, to="COLLECTING")
    reply = make(env, cid, "PRELIMINARY")
    assert reply.status_code == 409 and reply.json()["errors"][0]["code"] == "E-CONS-003"
    assert move(env, cid, "VALIDATING").status_code == 200
    assert make(env, cid, "PRELIMINARY").status_code == 201
    assert make(env, cid, "PRELIMINARY").status_code == 201  # again, from PRELIMINARY: version 2
    assert make(env, cid, "SOMETHING").status_code == 422


def test_who_may_make_and_who_may_read_a_result(env: Env, tmp_path: Path) -> None:
    send(env, tmp_path, "BIS")
    send(env, tmp_path, "OSC")
    cid = campaign(env)
    assert make(env, cid, "PRELIMINARY", "designer").status_code == 403
    assert make(env, cid, "PRELIMINARY", None).status_code == 401
    assert make(env, cid, "PRELIMINARY", "operator").status_code == 201
    for role in ("admin", "designer", "operator", "clerk"):
        assert env.call(role, "GET", f"/campaigns/{cid}/consolidations").status_code == 200
    assert env.call("admin", "GET", f"/campaigns/{cid}/consolidations/9").status_code == 404
    assert env.call("admin", "GET", "/campaigns/nothing/consolidations").status_code == 404
    assert make(env, "nothing", "PRELIMINARY").status_code == 404
