"""US-58: the workflow of a campaign is run from the campaign, and a run after a correction does
again only the steps whose inputs changed (api-spec 15)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.unit.actions.helpers import FIVE_ROWS, REGION_CODES, Row, write_form
from tests.unit.api.helpers import Env, headers, make_env
from tests.unit.api.test_campaigns import create, move, publish

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MORE: list[Row] = [("I01", "Population", 400.1), *FIVE_ROWS[1:]]


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


def campaign(env: Env, tmp_path: Path, to: str = "COLLECTING") -> str:
    publish(env)
    cid = str(create(env, rules="regional-indicators-v1")["id"])
    for step in ("OPEN", "COLLECTING", "VALIDATING"):
        assert move(env, cid, step).status_code == 200
        if step == to:
            break
    for region in REGION_CODES:
        send(env, tmp_path, region)
    return cid


def start(env: Env, cid: str, role: str | None = "operator"):  # type: ignore[no-untyped-def]
    return env.call(role, "POST", f"/campaigns/{cid}/runs")


def runs(env: Env, cid: str) -> list[dict[str, Any]]:
    reply = env.call("designer", "GET", f"/campaigns/{cid}/runs")
    assert reply.status_code == 200, reply.text
    return list(reply.json()["runs"])


def test_the_workflow_of_the_campaign_is_run_for_its_quarter_with_the_forms_as_they_are(
    env: Env, tmp_path: Path
) -> None:
    cid = campaign(env, tmp_path)
    send(env, tmp_path, "OSH", MORE)  # OSH is at its second version when the run starts
    reply = start(env, cid)
    assert reply.status_code == 202, reply.text
    started = reply.json()
    assert started["workflow"] == "quarterly_consolidation" and started["version"] == 1
    run = env.call("operator", "GET", f"/runs/{started['run_id']}").json()
    assert run["status"] == "WAITING"  # the approval of the chief statistician
    total = next(s for s in run["steps"] if s["step"] == "consolidate")["output"]["rows"][0]
    assert total["code"] == "I01" and total["value"] == pytest.approx(3600.1)
    (listed,) = runs(env, cid)
    assert listed["run_id"] == started["run_id"] and listed["started_by"] == "olga"
    assert listed["status"] == "WAITING"
    assert listed["forms"]["OSH"] == 2 and listed["forms"]["BIS"] == 1 and len(listed["forms"]) == 9
    assert (listed["executed"], listed["reused"]) == (19, 0)  # 9 loads, 9 checks, the total


def test_a_run_after_a_correction_does_again_only_what_the_correction_changed(
    env: Env, tmp_path: Path
) -> None:
    cid = campaign(env, tmp_path)
    assert start(env, cid).status_code == 202
    send(env, tmp_path, "OSH", MORE)
    second = start(env, cid).json()["run_id"]
    newest = runs(env, cid)[0]
    assert newest["run_id"] == second and newest["forms"]["OSH"] == 2
    assert (newest["executed"], newest["reused"]) == (3, 16)
    assert sorted(newest["executed_steps"]) == ["check[OSH]", "consolidate", "load[OSH]"]
    run = env.call("operator", "GET", f"/runs/{second}").json()
    states = {s["step"]: s["state"] for s in run["steps"]}
    assert states["load[BIS]"] == "SUCCEEDED" and states["consolidate"] == "SUCCEEDED"
    total = next(s for s in run["steps"] if s["step"] == "consolidate")["output"]["rows"][0]
    assert total["value"] == pytest.approx(3600.1)  # the new figure, not the one that was kept


def test_a_run_with_nothing_changed_repeats_no_step_that_only_computes(
    env: Env, tmp_path: Path
) -> None:
    cid = campaign(env, tmp_path)
    first = start(env, cid).json()["run_id"]
    second = start(env, cid).json()["run_id"]
    newest, oldest = runs(env, cid)
    assert (newest["run_id"], oldest["run_id"]) == (second, first)
    assert (newest["executed"], newest["reused"]) == (0, 19)
    one = env.call("operator", "GET", f"/runs/{first}").json()
    two = env.call("operator", "GET", f"/runs/{second}").json()

    def out(run: dict[str, Any]) -> dict[str, Any]:
        return {s["step"]: s["output"] for s in run["steps"]}

    assert out(one) == out(two)  # a kept result is the result


def test_a_step_with_an_effect_is_never_taken_from_an_earlier_run(env: Env, tmp_path: Path) -> None:
    cid = campaign(env, tmp_path)
    for _ in range(2):
        run_id = start(env, cid).json()["run_id"]
        decided = env.call(
            "operator", "POST", f"/runs/{run_id}/decisions",
            json={"step": "approve", "decision": "approved"},
        )  # fmt: skip
        assert decided.status_code == 202, decided.text
        run = env.call("operator", "GET", f"/runs/{run_id}").json()
        assert run["outcome"] == "SUCCEEDED"
        written = next(s for s in run["steps"] if s["step"] == "publish")["output"]
        assert written["batch"] == run_id  # written by this run, both times
    newest = runs(env, cid)[0]
    assert "publish" not in newest["reused_steps"] and "approve" not in newest["reused_steps"]
    assert newest["reused"] == 19  # what only computes was taken; the write was made again


def test_a_run_outside_a_campaign_keeps_and_takes_nothing(env: Env, tmp_path: Path) -> None:
    cid = campaign(env, tmp_path)
    assert start(env, cid).status_code == 202
    plain = env.call(
        "operator", "POST", "/workflows/quarterly_consolidation/runs",
        json={"inputs": {"quarter": "2026Q3"}},
    )  # fmt: skip
    assert plain.status_code == 202
    assert len(runs(env, cid)) == 1  # not a run of the campaign
    again = start(env, cid).json()["run_id"]
    assert runs(env, cid)[0]["run_id"] == again and runs(env, cid)[0]["reused"] == 19


def test_when_a_run_of_a_campaign_may_be_started_and_by_whom(env: Env, tmp_path: Path) -> None:
    publish(env)
    cid = str(create(env)["id"])
    refused = start(env, cid, "admin")
    assert refused.status_code == 409 and refused.json()["errors"][0]["code"] == "E-CAMP-009"
    assert move(env, cid, "OPEN").status_code == 200
    assert move(env, cid, "COLLECTING").status_code == 200
    assert start(env, cid, "designer").status_code == 202  # runs:start, as for any run
    assert start(env, cid, None).status_code == 401
    assert start(env, "nothing", "admin").status_code == 404
    assert env.call("clerk", "GET", "/campaigns/nothing/runs").status_code == 404
