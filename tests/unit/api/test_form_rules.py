"""US-54: what the API tells about the content of a form, and which rules a campaign asks for."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.unit.actions.helpers import REGION_CODES, Row, write_form
from tests.unit.api.helpers import EXAMPLE, NAME, Env, headers, make_env

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FIVE: list[Row] = [
    ("I01", "Population", 400),
    ("I02", "Households", 111.5),
    ("I03", "Births", 6.5),
    ("I04", "Deaths", 2.5),
    ("I05", "Marriages", 2.75),
]


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return make_env(tmp_path)


def put(env: Env, region: str, quarter: str, rows: list[Row], tmp_path: Path) -> Any:
    content = write_form(tmp_path / "made.xlsx", rows).read_bytes()
    return env.client.put(
        f"/forms/{region}/{quarter}",
        content=content,
        headers={**headers("operator"), "Content-Type": XLSX},
    )


def campaign(env: Env, **changes: Any) -> dict[str, Any]:
    assert (
        env.call(
            "designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE}
        ).status_code
        == 201
    )
    base: dict[str, Any] = {
        "name": "Q3 2026",
        "year": 2026,
        "quarter": 3,
        "regions": list(REGION_CODES),
        "deadline": "2026-10-31",
        "workflow": NAME,
        "workflow_version": 1,
    }
    reply = env.call("admin", "POST", "/campaigns", json={**base, **changes})
    assert reply.status_code == 201, reply.text
    made: dict[str, Any] = reply.json()
    return made


# -- the findings of a form ---------------------------------------------------------------------
def test_a_form_that_breaks_a_rule_tells_where_and_why(env: Env, tmp_path: Path) -> None:
    rows = list(FIVE)
    rows[3] = ("I04", "Deaths", -2.5)
    reply = put(env, "BAT", "2025Q1", rows, tmp_path)
    assert reply.status_code == 200
    (found,) = reply.json()["findings"]
    assert found["code"] == "VAL-RANGE-001" and found["severity"] == "error"
    assert (found["region"], found["file"]) == ("BAT", "BAT/2025Q1.xlsx")
    assert (found["cell"], found["indicator"], found["row"]) == ("C5", "I04", 5)
    assert (found["actual"], found["expected"]) == ("-2.5", "0 or more")
    assert found["sheet"]  # the name of the first sheet
    assert reply.json()["problems"] == [found["message"]]


def test_a_form_with_only_warnings_is_not_a_form_with_problems(env: Env, tmp_path: Path) -> None:
    reply = put(env, "BIS", "2025Q1", FIVE, tmp_path)
    assert reply.json()["findings"] == [] and reply.json()["problems"] == []


def test_without_a_campaign_the_rules_are_the_basic_ones(env: Env, tmp_path: Path) -> None:
    reply = put(env, "BIS", "2025Q1", FIVE[:2], tmp_path)  # three indicators are missing
    assert reply.json()["findings"] == [] and reply.json()["problems"] == []


# -- the rules of the campaign ------------------------------------------------------------------
def test_the_campaign_of_a_quarter_chooses_the_rules_for_its_forms(
    env: Env, tmp_path: Path
) -> None:
    campaign(env, rules="regional-indicators-v1")
    reply = put(env, "BIS", "2026Q3", FIVE[:2], tmp_path)
    found = reply.json()["findings"]
    assert [f["code"] for f in found] == ["VAL-COMP-001"] * 3
    assert {f["indicator"] for f in found} == {"I03", "I04", "I05"}
    assert all(f["cell"] == "" for f in found)  # there is no cell for what is not there
    assert reply.json()["problems"][0] == "I03 Births is missing"
    other = put(env, "BIS", "2026Q4", FIVE[:2], tmp_path)  # another quarter: no campaign
    assert other.json()["findings"] == []


def test_a_relation_between_indicators_blocks_the_form(env: Env, tmp_path: Path) -> None:
    campaign(env, rules="regional-indicators-v1")
    rows = list(FIVE)
    rows[2] = ("I03", "Births", 450)  # more births than people
    found = put(env, "OSC", "2026Q3", rows, tmp_path).json()["findings"]
    assert [(f["code"], f["indicator"], f["cell"]) for f in found] == [("VAL-REL-001", "I03", "C4")]
    assert found[0]["expected"] == "at most 400 (I01)"


def test_the_progress_counts_errors_and_warnings_apart(env: Env, tmp_path: Path) -> None:
    made = campaign(env, rules="regional-indicators-v1")
    put(env, "BIS", "2026Q3", FIVE, tmp_path)
    put(env, "OSC", "2026Q3", [*FIVE, ("X9", "Other", 1)], tmp_path)  # a warning
    put(env, "BAT", "2026Q3", FIVE[:3], tmp_path)  # two indicators missing
    got = env.call("operator", "GET", f"/campaigns/{made['id']}").json()["progress"]
    by = {r["region"]: r for r in got["regions"]}
    assert (by["BIS"]["valid"], by["BIS"]["warnings"]) == (True, 0)
    assert (by["OSC"]["valid"], by["OSC"]["problems"], by["OSC"]["warnings"]) == (True, 0, 1)
    assert (by["BAT"]["valid"], by["BAT"]["problems"]) == (False, 2)
    assert (got["valid"], got["invalid"]) == (2, 1)


def test_the_rules_of_a_campaign_must_exist(env: Env) -> None:
    assert (
        env.call(
            "designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE}
        ).status_code
        == 201
    )
    reply = env.call(
        "admin",
        "POST",
        "/campaigns",
        json={
            "name": "x",
            "year": 2026,
            "quarter": 3,
            "regions": ["BIS"],
            "deadline": "2026-10-31",
            "workflow": NAME,
            "workflow_version": 1,
            "rules": "strict",
        },
    )
    error = reply.json()["errors"][0]
    assert reply.status_code == 422 and error["code"] == "E-CAMP-001" and error["field"] == "rules"
