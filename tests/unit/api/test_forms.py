"""US-49: the regional forms are received, listed and removed through the API (api-spec 11)."""

from __future__ import annotations

from pathlib import Path

import pytest

from flowstat.service.forms import MAX_BYTES
from tests.unit.actions.helpers import GOOD_ROWS, Row, write_form
from tests.unit.api.helpers import Env, headers, make_env

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return make_env(tmp_path)


def workbook(
    tmp_path: Path, rows: list[Row] | None = None, header: Row = ("code", "name", "value")
) -> bytes:
    path = write_form(tmp_path / "made.xlsx", GOOD_ROWS if rows is None else rows, header)
    return path.read_bytes()


def put(env: Env, role: str | None, path: str, content: bytes):  # type: ignore[no-untyped-def]
    return env.client.put(path, content=content, headers={**headers(role), "Content-Type": XLSX})


def test_an_operator_sends_a_form_and_it_is_where_the_workflow_reads_it(
    env: Env, tmp_path: Path
) -> None:
    reply = put(env, "operator", "/forms/BIS/2025Q1", workbook(tmp_path))
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert (body["region"], body["name"], body["quarter"]) == ("BIS", "Bis", "2025Q1")
    assert body["received"] is True and body["rows"] == 3 and body["problems"] == []
    assert body["size"] == (env.config.data_dir / "BIS" / "2025Q1.xlsx").stat().st_size
    assert list((env.config.data_dir / "BIS").iterdir()) == [
        env.config.data_dir / "BIS" / "2025Q1.xlsx"
    ]


def test_the_listing_tells_for_each_region_whether_its_form_came(env: Env, tmp_path: Path) -> None:
    put(env, "admin", "/forms/OSC/2025Q1", workbook(tmp_path))
    reply = env.call("designer", "GET", "/forms?quarter=2025Q1")
    assert reply.status_code == 200
    forms = reply.json()["forms"]
    assert [f["region"] for f in forms] == [
        "BIS",
        "OSC",
        "BAT",
        "JAL",
        "ISK",
        "NAR",
        "OSH",
        "TAL",
        "CHU",
    ]
    assert [f["region"] for f in forms if f["received"]] == ["OSC"]
    assert forms[0] == {
        "region": "BIS", "name": "Bis", "quarter": "2025Q1", "received": False,
        "rows": None, "size": None, "problems": [], "findings": [],
    }  # fmt: skip
    assert (
        env.call("operator", "GET", "/forms?quarter=2024Q4").json()["forms"][1]["received"] is False
    )


def test_a_designer_may_read_the_forms_but_not_send_or_remove_them(
    env: Env, tmp_path: Path
) -> None:
    assert put(env, "designer", "/forms/BIS/2025Q1", workbook(tmp_path)).status_code == 403
    assert env.call("designer", "DELETE", "/forms/BIS/2025Q1").status_code == 403
    assert put(env, None, "/forms/BIS/2025Q1", workbook(tmp_path)).status_code == 401
    assert env.call(None, "GET", "/forms?quarter=2025Q1").status_code == 401


def test_a_region_that_is_not_in_the_catalogue_and_a_path_trick_are_not_found(
    env: Env, tmp_path: Path
) -> None:
    data = workbook(tmp_path)
    for region in ("XXX", "..%2F..", "bis", "BIS%2F..%2FOSC"):
        reply = put(env, "operator", f"/forms/{region}/2025Q1", data)
        assert reply.status_code == 404, region
        assert reply.json()["errors"][0]["code"] == "E-API-002"
    assert not (env.config.data_dir.parent / "2025Q1.xlsx").exists()


def test_a_catalogue_code_that_is_not_safe_as_a_folder_name_is_never_used(
    env: Env, tmp_path: Path
) -> None:
    env.call("admin", "PUT", "/catalogue/regions", json={"items": [
        {"code": "OK1", "name": "Fine"}, {"code": "../evil", "name": "Bad"},
    ]})  # fmt: skip
    listed = env.call("operator", "GET", "/forms?quarter=2025Q1").json()["forms"]
    assert [f["region"] for f in listed] == ["OK1"]


@pytest.mark.parametrize("quarter", ["2025", "2025Q5", "25Q1", "2025q1", "2025Q1x"])
def test_a_quarter_that_does_not_look_like_one_is_refused(
    env: Env, tmp_path: Path, quarter: str
) -> None:
    reply = put(env, "operator", f"/forms/BIS/{quarter}", workbook(tmp_path))
    assert reply.status_code == 422 and reply.json()["errors"][0]["code"] == "E-FORM-001"
    assert env.call("operator", "GET", f"/forms?quarter={quarter}").status_code == 422


def test_a_file_that_is_not_a_form_is_refused_and_does_not_replace_the_good_one(
    env: Env, tmp_path: Path
) -> None:
    put(env, "operator", "/forms/BIS/2025Q1", workbook(tmp_path))
    before = (env.config.data_dir / "BIS" / "2025Q1.xlsx").read_bytes()
    for content in (b"this is text", b"", workbook(tmp_path, header=("a", "b", "c"))):
        reply = put(env, "operator", "/forms/BIS/2025Q1", content)
        assert reply.status_code == 422, content[:20]
        error = reply.json()["errors"][0]
        assert error["code"] == "E-FORM-001" and ".upload" not in error["message"]
    assert (env.config.data_dir / "BIS" / "2025Q1.xlsx").read_bytes() == before
    assert [p.name for p in (env.config.data_dir / "BIS").iterdir()] == ["2025Q1.xlsx"]


def test_a_form_that_breaks_the_rules_is_accepted_and_the_sender_is_told(
    env: Env, tmp_path: Path
) -> None:
    rows: list[Row] = [("I01", "A", 5), ("I01", "B", -2), ("I03", "C", "many")]
    reply = put(env, "operator", "/forms/BAT/2025Q1", workbook(tmp_path, rows))
    assert reply.status_code == 200
    problems = reply.json()["problems"]
    assert len(problems) == 3
    assert "duplicate code I01" in problems[0] and "negative" in problems[1]
    listed = env.call("operator", "GET", "/forms?quarter=2025Q1").json()["forms"]
    assert next(f for f in listed if f["region"] == "BAT")["problems"] == problems


def test_a_long_list_of_violations_is_cut_and_counted(env: Env, tmp_path: Path) -> None:
    rows: list[Row] = [(f"C{n}", "x", -1) for n in range(9)]
    problems = put(env, "operator", "/forms/BAT/2025Q1", workbook(tmp_path, rows)).json()[
        "problems"
    ]
    assert len(problems) == 6 and problems[-1] == "and 4 more"


def test_a_second_form_replaces_the_first(env: Env, tmp_path: Path) -> None:
    put(env, "operator", "/forms/BIS/2025Q1", workbook(tmp_path))
    reply = put(env, "operator", "/forms/BIS/2025Q1", workbook(tmp_path, [("I01", "Only", 1)]))
    assert reply.status_code == 200 and reply.json()["rows"] == 1


def test_a_form_that_is_too_big_is_refused_with_413(env: Env) -> None:
    reply = put(env, "operator", "/forms/BIS/2025Q1", b"x" * (MAX_BYTES + 1))
    assert reply.status_code == 413 and reply.json()["errors"][0]["code"] == "E-FORM-002"
    assert not (env.config.data_dir / "BIS" / "2025Q1.xlsx").exists()


def test_a_form_can_be_removed_once(env: Env, tmp_path: Path) -> None:
    put(env, "operator", "/forms/BIS/2025Q1", workbook(tmp_path))
    assert env.call("operator", "DELETE", "/forms/BIS/2025Q1").status_code == 204
    assert not (env.config.data_dir / "BIS" / "2025Q1.xlsx").exists()
    assert env.call("operator", "DELETE", "/forms/BIS/2025Q1").status_code == 404


def test_a_file_that_was_put_in_the_folder_by_hand_and_cannot_be_read_is_listed_with_the_reason(
    env: Env,
) -> None:
    folder = env.config.data_dir / "JAL"
    folder.mkdir(parents=True)
    (folder / "2025Q1.xlsx").write_bytes(b"not a workbook")
    form = next(
        f
        for f in env.call("operator", "GET", "/forms?quarter=2025Q1").json()["forms"]
        if f["region"] == "JAL"
    )
    assert form["received"] is True and form["rows"] is None
    assert "not a valid xlsx" in form["problems"][0]


# -- the whole cycle: forms sent through the API are what a run reads --------------------------
def finished(env: Env, run_id: str) -> dict[str, object]:
    import time

    for _ in range(300):
        report: dict[str, object] = env.call("admin", "GET", f"/runs/{run_id}").json()
        if report["status"] != "RUNNING":
            return report
        time.sleep(0.1)
    raise AssertionError("the run did not stop")


def start_run(env: Env) -> str:
    from tests.unit.api.helpers import EXAMPLE, NAME

    env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish")
    started = env.call(
        "operator", "POST", f"/workflows/{NAME}/runs",
        json={"version": 1, "inputs": {"quarter": "2025Q1"}},
    )  # fmt: skip
    assert started.status_code == 202, started.text
    run_id: str = started.json()["run_id"]
    return run_id


def send_all(env: Env, tmp_path: Path, bad: dict[str, list[Row]]) -> None:
    for region in ("BIS", "OSC", "BAT", "JAL", "ISK", "NAR", "OSH", "TAL", "CHU"):
        data = workbook(tmp_path, bad.get(region))
        assert put(env, "operator", f"/forms/{region}/2025Q1", data).status_code == 200


def test_nine_good_forms_sent_through_the_api_take_a_run_to_the_approval(
    env: Env, tmp_path: Path
) -> None:
    send_all(env, tmp_path, {})
    report = finished(env, start_run(env))
    assert report["status"] == "WAITING"
    states = {s["step"]: s["state"] for s in report["steps"]}  # type: ignore[attr-defined]
    assert states["check[BIS]"] == "SUCCEEDED" and states["approve"] == "WAITING"


def test_a_form_with_a_mistake_stops_the_run_at_its_check_and_says_where(
    env: Env, tmp_path: Path
) -> None:
    send_all(env, tmp_path, {"BAT": [("I01", "A", -5)]})
    report = finished(env, start_run(env))
    steps = {s["step"]: s for s in report["steps"]}  # type: ignore[attr-defined]
    assert steps["check[BAT]"]["state"] == "FAILED" and steps["check[BAT]"]["code"] == "E-ACT-005"
    assert "value is negative" in str(steps["check[BAT]"]["message"])
    assert steps["approve"]["state"] != "WAITING"  # nothing reaches the chief statistician
    assert report["outcome"] == "FAILED"


def test_a_region_that_sent_nothing_is_tolerated_up_to_two_and_not_beyond(
    env: Env, tmp_path: Path
) -> None:
    send_all(env, tmp_path, {})
    env.call("operator", "DELETE", "/forms/BIS/2025Q1")
    assert (
        finished(env, start_run(env))["status"] == "WAITING"
    )  # eight of nine: the quorum is seven
    for region in ("OSC", "BAT"):
        env.call("operator", "DELETE", f"/forms/{region}/2025Q1")
    again = env.call(
        "operator", "POST", "/workflows/quarterly_consolidation/runs",
        json={"version": 1, "inputs": {"quarter": "2025Q1"}},
    )  # fmt: skip
    assert finished(env, again.json()["run_id"])["outcome"] == "GOAL_NOT_REACHED"  # six of nine
