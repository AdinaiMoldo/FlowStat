"""US-61: the chief statistician approves a result, rejects it or asks for a correction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.unit.actions.helpers import FIVE_ROWS, Row, write_form
from tests.unit.api.helpers import Env, headers, make_env
from tests.unit.api.test_campaigns import create, move, publish

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MORE: list[Row] = [("I01", "Population", 400.1), *FIVE_ROWS[1:]]
REGIONS = ["BIS", "OSC", "BAT"]


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return make_env(tmp_path)


def send(env: Env, tmp_path: Path, region: str, rows: list[Row] = FIVE_ROWS):  # type: ignore[no-untyped-def]
    content = write_form(tmp_path / "made.xlsx", rows).read_bytes()
    return env.client.put(
        f"/forms/{region}/2026Q3",
        content=content,
        headers={**headers("admin"), "Content-Type": XLSX},
    )


def pending(env: Env, tmp_path: Path) -> str:
    """A campaign of three regions with a final result that waits for the approval."""
    publish(env)
    cid = str(create(env, regions=REGIONS, rules="regional-indicators-v1",
                     preliminary_min=2, final_min=3)["id"])  # fmt: skip
    for step in ("OPEN", "COLLECTING", "VALIDATING"):
        assert move(env, cid, step).status_code == 200
    for region in REGIONS:
        assert send(env, tmp_path, region).status_code == 200
    made = env.call("admin", "POST", f"/campaigns/{cid}/consolidations", json={"kind": "FINAL"})
    assert made.status_code == 201, made.text
    return cid


def decide(
    env: Env,
    cid: str,
    number: int,
    decision: str,
    comment: str = "",
    role: str | None = "operator",
) -> Any:
    return env.call(
        role, "POST", f"/campaigns/{cid}/consolidations/{number}/decision",
        json={"decision": decision, "comment": comment},
    )  # fmt: skip


def campaign(env: Env, cid: str) -> dict[str, Any]:
    found: dict[str, Any] = env.call("admin", "GET", f"/campaigns/{cid}").json()
    return found


def code(reply: Any) -> str:
    return str(reply.json()["errors"][0]["code"])


def test_the_chief_statistician_approves_the_result_and_the_forms_are_locked(
    env: Env, tmp_path: Path
) -> None:
    cid = pending(env, tmp_path)
    reply = decide(env, cid, 1, "APPROVE")  # olga holds the title chief_statistician
    assert reply.status_code == 200, reply.text
    decision = reply.json()["decision"]
    assert (decision["decision"], decision["by"], decision["comment"]) == ("APPROVE", "olga", "")
    assert decision["at"]
    assert campaign(env, cid)["status"] == "APPROVED"
    read = env.call("designer", "GET", f"/campaigns/{cid}/consolidations/1").json()
    assert read["decision"]["decision"] == "APPROVE" and read["stale"] is False
    locked = send(env, tmp_path, "BIS", MORE)
    assert locked.status_code == 409 and code(locked) == "E-CAMP-006"


def test_a_rejection_needs_a_comment_and_is_kept_with_it(env: Env, tmp_path: Path) -> None:
    cid = pending(env, tmp_path)
    bare = decide(env, cid, 1, "REJECT")
    assert bare.status_code == 422 and code(bare) == "E-CONS-008"
    assert campaign(env, cid)["status"] == "PENDING_APPROVAL"
    reply = decide(env, cid, 1, "REJECT", "the households of BAT cannot be right")
    assert reply.status_code == 200
    assert reply.json()["decision"]["comment"] == "the households of BAT cannot be right"
    assert campaign(env, cid)["status"] == "REJECTED"


def test_a_correction_sends_the_campaign_back_and_the_next_result_is_not_approved_by_inheritance(
    env: Env, tmp_path: Path
) -> None:
    cid = pending(env, tmp_path)
    assert decide(env, cid, 1, "CORRECT").status_code == 422
    asked = decide(env, cid, 1, "CORRECT", "BIS: the population is the figure of last year")
    assert asked.status_code == 200 and campaign(env, cid)["status"] == "COLLECTING"
    assert send(env, tmp_path, "BIS", MORE).status_code == 200
    assert move(env, cid, "VALIDATING").status_code == 200
    made = env.call("admin", "POST", f"/campaigns/{cid}/consolidations", json={"kind": "FINAL"})
    assert made.status_code == 201 and made.json()["number"] == 2
    assert made.json()["decision"] is None  # a new result version: nobody has decided about it
    old = env.call("admin", "GET", f"/campaigns/{cid}/consolidations/1").json()
    assert old["decision"]["decision"] == "CORRECT" and old["stale"] is True
    late = decide(env, cid, 1, "APPROVE")
    assert late.status_code == 409 and code(late) in ("E-CONS-005", "E-CONS-007")
    assert decide(env, cid, 2, "APPROVE").status_code == 200
    assert campaign(env, cid)["status"] == "APPROVED"


def test_a_result_is_decided_once(env: Env, tmp_path: Path) -> None:
    cid = pending(env, tmp_path)
    assert decide(env, cid, 1, "APPROVE").status_code == 200
    again = decide(env, cid, 1, "APPROVE")
    assert again.status_code == 409 and code(again) in ("E-CONS-006", "E-CONS-007")
    assert decide(env, cid, 1, "REJECT", "changed my mind").status_code == 409


def test_a_preliminary_result_and_a_stale_result_cannot_be_approved(
    env: Env, tmp_path: Path
) -> None:
    publish(env)
    cid = str(create(env, regions=REGIONS, rules="regional-indicators-v1",
                     preliminary_min=2, final_min=3)["id"])  # fmt: skip
    for step in ("OPEN", "COLLECTING", "VALIDATING"):
        assert move(env, cid, step).status_code == 200
    for region in REGIONS:
        send(env, tmp_path, region)
    env.call("admin", "POST", f"/campaigns/{cid}/consolidations", json={"kind": "PRELIMINARY"})
    early = decide(env, cid, 1, "APPROVE")
    assert early.status_code == 409 and code(early) == "E-CONS-006"
    env.call("admin", "POST", f"/campaigns/{cid}/consolidations", json={"kind": "FINAL"})
    send(env, tmp_path, "OSC", MORE)  # a form changes while the result waits
    stale = decide(env, cid, 2, "APPROVE")
    assert stale.status_code == 409 and code(stale) == "E-CONS-005"
    assert campaign(env, cid)["status"] == "COLLECTING"


def test_who_may_decide(env: Env, tmp_path: Path) -> None:
    cid = pending(env, tmp_path)
    clerk = decide(env, cid, 1, "APPROVE", role="clerk")  # an operator without the title
    assert clerk.status_code == 403 and code(clerk) == "E-AUTH-002"
    assert "chief_statistician" in clerk.json()["errors"][0]["message"]
    assert decide(env, cid, 1, "APPROVE", role="designer").status_code == 403
    assert decide(env, cid, 1, "APPROVE", role=None).status_code == 401
    assert decide(env, cid, 9, "APPROVE").status_code == 404
    assert decide(env, "nothing", 1, "APPROVE").status_code == 404
    assert decide(env, cid, 1, "MAYBE").status_code == 422
    assert campaign(env, cid)["status"] == "PENDING_APPROVAL"
    assert (
        decide(env, cid, 1, "APPROVE", role="admin").status_code == 200
    )  # an admin needs no title
