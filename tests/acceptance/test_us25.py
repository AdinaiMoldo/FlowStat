"""Acceptance test of US-25: an external system starts a run over REST.

Given a published workflow, when I POST valid inputs, then I receive a run id. Invalid inputs
return 422 with field errors. (And DR-04: only the chief statistician approves a publication.)
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.actions.helpers import write_all_forms
from tests.unit.api.helpers import EXAMPLE, NAME, Env, make_env


def published(tmp_path: Path) -> Env:
    env = make_env(tmp_path)
    env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish")
    write_all_forms(env.config.data_dir, "2025Q1")
    return env


def test_valid_inputs_give_a_run_id_and_the_run_can_be_followed(tmp_path: Path) -> None:
    env = published(tmp_path)
    response = env.call(
        "operator", "POST", f"/workflows/{NAME}/runs", json={"inputs": {"quarter": "2025Q1"}}
    )
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    assert run_id
    report = env.call("operator", "GET", f"/runs/{run_id}").json()
    assert report["run_id"] == run_id and report["status"] == "WAITING"


def test_invalid_inputs_are_422_with_a_field_error_each_and_no_run_is_made(tmp_path: Path) -> None:
    env = published(tmp_path)
    response = env.call(
        "operator",
        "POST",
        f"/workflows/{NAME}/runs",
        json={"inputs": {"quarter": "Q5 2025", "extra": 1}},
    )
    assert response.status_code == 422
    errors = response.json()["errors"]
    assert {e["field"] for e in errors} == {"inputs.quarter", "inputs.extra"}
    assert all(e["code"].startswith("E-INPUT-") for e in errors)
    assert env.call("operator", "GET", "/runs").json() == []


def test_a_publication_waits_for_the_chief_statistician(tmp_path: Path) -> None:
    """DR-04: nobody else can approve it, and the answer names the person who gave it."""
    env = published(tmp_path)
    env.call("operator", "POST", f"/workflows/{NAME}/runs", json={"inputs": {"quarter": "2025Q1"}})
    answer = {"step": "approve", "decision": "approved"}
    assert env.call("clerk", "POST", "/runs/r-0001/decisions", json=answer).status_code == 403
    assert env.call("operator", "GET", "/runs/r-0001").json()["status"] == "WAITING"
    assert env.call("operator", "POST", "/runs/r-0001/decisions", json=answer).status_code == 202
    report = env.call("operator", "GET", "/runs/r-0001").json()
    assert report["status"] == "FINISHED" and report["outcome"] == "SUCCEEDED"
    approve = next(s for s in report["steps"] if s["step"] == "approve")
    assert approve["output"]["by"] == "olga"
