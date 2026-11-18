"""US-52: the demonstration can start with a history, so that no page of it is empty.

The history is made in the process, before the core starts, with a clock that is given to it, so
the days of the runs do not depend on the day the test is run.
"""

from __future__ import annotations

import importlib
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load(name: str) -> ModuleType:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        return importlib.import_module(name)
    finally:
        sys.path.remove(str(ROOT / "scripts"))


demo = load("demo")
seed_history = load("seed_history")

NOW = datetime(2026, 11, 18, 15, 0, tzinfo=UTC)


@pytest.fixture
def seeded(tmp_path: Path):  # type: ignore[no-untyped-def]
    """A folder with the users of the demonstration and the history made in it."""
    work = tmp_path / "exercise"
    work.mkdir()
    db = str(work / "flowstat.db")
    tokens = {
        "admin": demo.make_user(db, "adinai", "admin"),
        "designer": demo.make_user(db, "azamat", "designer"),
        "chief": demo.make_user(db, "alua", "operator", "chief_statistician"),
        "operator": demo.make_user(db, "atai", "operator"),
        "operator2": demo.make_user(db, "marina", "operator"),
    }
    summary = seed_history.seed(work, db, tokens, NOW)
    client = seed_history.open_client(work, db, NOW)
    return summary, client, tokens


def runs(seeded) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    _, client, tokens = seeded
    reply = client.get("/runs", headers={"Authorization": f"Bearer {tokens['admin']}"})
    assert reply.status_code == 200
    found: list[dict[str, Any]] = reply.json()
    return found


def test_the_history_has_eight_finished_runs_and_one_that_waits(seeded) -> None:  # type: ignore[no-untyped-def]
    found = runs(seeded)
    assert Counter(r["status"] for r in found) == {"FINISHED": 8, "WAITING": 1}
    # six reached the goal, one stopped at a form with a mistake, one did not reach the goal
    outcomes = Counter(r["outcome"] for r in found if r["status"] == "FINISHED")
    assert outcomes == {"SUCCEEDED": 6, "FAILED": 1, "GOAL_NOT_REACHED": 1}


def test_the_runs_are_spread_over_the_last_days_and_none_is_in_the_future(seeded) -> None:  # type: ignore[no-untyped-def]
    found = runs(seeded)
    created = sorted(datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) for r in found)
    assert created[-1] <= NOW
    assert NOW - created[0] <= timedelta(days=14)
    assert len({c.date() for c in created}) >= 6  # not all on one day


def test_the_failed_run_stopped_at_the_form_with_the_mistake(seeded) -> None:  # type: ignore[no-untyped-def]
    _, client, tokens = seeded
    failed = next(r for r in runs(seeded) if r["outcome"] == "FAILED")
    report = client.get(
        f"/runs/{failed['run_id']}", headers={"Authorization": f"Bearer {tokens['admin']}"}
    ).json()
    bad = [s for s in report["steps"] if s["state"] == "FAILED"]
    assert [s["step"] for s in bad] == ["check[BAT]"] and bad[0]["code"] == "E-ACT-005"


def test_the_waiting_run_can_be_answered_by_the_chief_statistician(seeded) -> None:  # type: ignore[no-untyped-def]
    _, client, tokens = seeded
    waiting = next(r for r in runs(seeded) if r["status"] == "WAITING")
    reply = client.post(
        f"/runs/{waiting['run_id']}/decisions",
        headers={"Authorization": f"Bearer {tokens['chief']}"},
        json={"step": "approve", "decision": "approved"},
    )
    assert reply.status_code == 202


def test_the_workflow_is_published_and_the_statistics_have_numbers(seeded) -> None:  # type: ignore[no-untyped-def]
    _, client, tokens = seeded
    admin = {"Authorization": f"Bearer {tokens['admin']}"}
    [workflow] = client.get("/workflows", headers=admin).json()
    assert workflow["name"] == "quarterly_consolidation" and workflow["latest_published"] == 2
    [stats] = client.get("/statistics", headers=admin).json()["workflows"]
    assert (stats["runs"], stats["succeeded"], stats["failed"], stats["not_reached"]) == (
        9,
        6,
        1,
        1,
    )
    assert stats["average_duration"] is not None and stats["average_duration"] > 0


def test_the_runs_were_started_by_the_operators_and_not_all_by_one(seeded) -> None:  # type: ignore[no-untyped-def]
    summary, _, _ = seeded
    assert set(summary["started_by"]) == {"atai", "marina"}


def test_the_forms_of_the_quarters_are_in_the_folder_the_core_reads(seeded, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    summary, _, _ = seeded
    data = tmp_path / "exercise" / "forms"
    for quarter in summary["quarters"]:
        assert (data / "BIS" / f"{quarter}.xlsx").exists()
    assert not (data / "BIS" / "2025Q1.xlsx").exists()  # the quarter of the live demonstration


def test_the_run_that_waits_can_be_moved_to_the_second_version(seeded) -> None:  # type: ignore[no-untyped-def]
    _, client, tokens = seeded
    reply = client.get(
        "/workflows/quarterly_consolidation/migration",
        params={"from": 1, "to": 2},
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    assert reply.status_code == 200
    [waiting] = reply.json()["runs"]
    assert waiting["migratable"] is True


def test_there_are_two_campaigns_one_validating_and_one_collecting(seeded) -> None:  # type: ignore[no-untyped-def]
    summary, client, tokens = seeded
    assert summary["campaigns"] == 2
    found = client.get("/campaigns", headers={"Authorization": f"Bearer {tokens['admin']}"}).json()
    by_period = {c["period"]: c for c in found}
    assert by_period["2024Q4"]["status"] == "VALIDATING"
    assert by_period["2025Q1"]["status"] == "COLLECTING"
    # the forms of 2024Q4 were written for its run: all nine are there and valid
    assert by_period["2024Q4"]["progress"]["valid"] == 9
    # the quarter of the live demonstration has none yet
    assert by_period["2025Q1"]["progress"]["received"] == 0
    # the deadline of the first has passed and the second is ahead, on the clock of the seed
    assert by_period["2024Q4"]["deadline"] < "2026-11-18" < by_period["2025Q1"]["deadline"]
