"""Tests of the run routes: start, report, list, the answer to an approval, the lock of a run."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx2
import pytest

from tests.unit.actions.helpers import write_all_forms
from tests.unit.api.helpers import EXAMPLE, NAME, Env, make_env

INPUTS = {"inputs": {"quarter": "2025Q1"}}


def body(response: httpx2.Response) -> dict[str, Any]:
    result: dict[str, Any] = response.json()
    return result


def published(env: Env) -> Env:
    env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    assert env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish").status_code == 200
    write_all_forms(env.config.data_dir, "2025Q1")
    return env


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return published(make_env(tmp_path))


def start(env: Env, role: str = "operator", **extra: Any) -> httpx2.Response:
    return env.call(role, "POST", f"/workflows/{NAME}/runs", json=INPUTS | extra)


def test_a_run_is_stored_and_answered_with_202_and_its_id(env: Env) -> None:
    response = start(env)
    assert response.status_code == 202
    assert body(response) == {
        "run_id": "r-0001",
        "workflow": NAME,
        "version": 1,
        "status": "CREATED",
    }


def test_the_run_reaches_the_approval_and_the_report_says_so(env: Env) -> None:
    start(env)
    report = body(env.call("operator", "GET", "/runs/r-0001"))
    assert report["status"] == "WAITING" and report["outcome"] is None
    states = {s["step"]: s["state"] for s in report["steps"]}
    assert states["approve"] == "WAITING" and states["consolidate"] == "SUCCEEDED"
    assert report["workflow"] == NAME and report["version"] == 1


def test_the_designer_may_start_a_run_too(env: Env) -> None:
    assert start(env, "designer").status_code == 202


def test_the_latest_published_version_is_the_default_and_a_given_one_must_exist(env: Env) -> None:
    assert body(start(env))["version"] == 1
    gone = start(env, version=9)
    assert gone.status_code == 404 and body(gone)["errors"][0]["code"] == "E-API-002"


def test_a_draft_cannot_run_and_a_workflow_without_a_publication_says_so(env: Env) -> None:
    env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})  # v2
    draft = start(env, version=2)
    assert draft.status_code == 409 and body(draft)["errors"][0]["code"] == "E-API-003"
    other = env.call("operator", "POST", "/workflows/nothing/runs", json=INPUTS)
    assert other.status_code == 404
    env.call("designer", "POST", "/workflows/fresh/versions", json={"text": "version: 1\n"})
    unpublished = env.call("operator", "POST", "/workflows/fresh/runs", json=INPUTS)
    assert unpublished.status_code == 409 and "no published version" in str(body(unpublished))


def test_invalid_inputs_give_one_error_per_input_and_store_nothing(env: Env) -> None:
    response = env.call(
        "operator", "POST", f"/workflows/{NAME}/runs", json={"inputs": {"quarter": "nope"}}
    )
    assert response.status_code == 422
    [error] = body(response)["errors"]
    assert error["code"].startswith("E-INPUT-") and error["field"] == "inputs.quarter"
    assert env.call("operator", "GET", "/runs").json() == []
    missing = env.call("operator", "POST", f"/workflows/{NAME}/runs", json={})
    assert missing.status_code == 422 and body(missing)["errors"][0]["field"] == "inputs.quarter"


def test_an_unknown_field_in_the_request_is_422(env: Env) -> None:
    response = env.call("operator", "POST", f"/workflows/{NAME}/runs", json=INPUTS | {"vers": 1})
    assert response.status_code == 422
    assert body(response)["errors"][0]["code"] == "E-API-001"


def test_unknown_run_is_404(env: Env) -> None:
    assert env.call("operator", "GET", "/runs/nothing").status_code == 404


def test_runs_are_listed_newest_first_and_can_be_filtered(env: Env) -> None:
    start(env)
    start(env)
    listing = body_list(env.call("operator", "GET", "/runs"))
    assert [r["run_id"] for r in listing] == ["r-0002", "r-0001"]
    assert {r["status"] for r in listing} == {"WAITING"}
    assert env.call("operator", "GET", "/runs?workflow=other").json() == []
    assert len(body_list(env.call("operator", "GET", "/runs?status=WAITING"))) == 2
    assert env.call("operator", "GET", "/runs?status=FINISHED").json() == []


def body_list(response: httpx2.Response) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = response.json()
    return result


# -- the answer to an approval ----------------------------------------------------------------
def decide(
    env: Env, role: str, decision: str = "approved", step: str = "approve"
) -> httpx2.Response:
    return env.call(
        role, "POST", "/runs/r-0001/decisions", json={"step": step, "decision": decision}
    )


def test_the_chief_statistician_approves_and_the_run_finishes(env: Env) -> None:
    start(env)
    response = decide(env, "operator")
    assert response.status_code == 202
    assert body(response) == {
        "run_id": "r-0001",
        "step": "approve",
        "decision": "approved",
        "by": "olga",
    }
    report = body(env.call("operator", "GET", "/runs/r-0001"))
    assert report["status"] == "FINISHED" and report["outcome"] == "SUCCEEDED"
    approve = next(s for s in report["steps"] if s["step"] == "approve")
    assert approve["output"] == {"decision": "approved", "by": "olga"}


def test_a_rejection_is_an_answer_too(env: Env) -> None:
    start(env)
    assert decide(env, "operator", "rejected").status_code == 202
    report = body(env.call("operator", "GET", "/runs/r-0001"))
    states = {s["step"]: s["state"] for s in report["steps"]}
    assert report["status"] == "FINISHED" and states["publish"] == "SKIPPED"


def test_an_operator_without_the_title_gets_403_and_the_run_still_waits(env: Env) -> None:
    start(env)
    response = decide(env, "clerk")
    assert response.status_code == 403
    assert body(response)["errors"][0]["code"] == "E-AUTH-002"
    assert "chief_statistician" in body(response)["errors"][0]["message"]
    assert body(env.call("operator", "GET", "/runs/r-0001"))["status"] == "WAITING"
    assert not env.client.app.state.locks.busy("r-0001")  # type: ignore[attr-defined]


def test_an_admin_may_answer_without_the_title(env: Env) -> None:
    start(env)
    assert decide(env, "admin").status_code == 202
    assert body(env.call("admin", "GET", "/runs/r-0001"))["status"] == "FINISHED"


def test_a_designer_may_not_answer_at_all(env: Env) -> None:
    start(env)
    assert decide(env, "designer").status_code == 403


def test_a_decision_the_step_does_not_declare_is_422_e_api_005(env: Env) -> None:
    start(env)
    response = decide(env, "operator", "maybe")
    assert response.status_code == 422
    [error] = body(response)["errors"]
    assert error["code"] == "E-API-005" and error["field"] == "decision"
    assert "approved, rejected" in error["message"]


def test_the_checks_have_a_fixed_order_and_the_other_failures_have_their_codes(env: Env) -> None:
    start(env)
    assert decide(env, "operator", step="consolidate").status_code == 409  # not waiting
    assert decide(env, "operator", step="nothing").status_code == 404
    unknown = env.call(
        "operator", "POST", "/runs/none/decisions", json={"step": "a", "decision": "b"}
    )
    assert unknown.status_code == 404
    both_wrong = decide(env, "clerk", "maybe")  # a wrong decision and no title: the decision first
    assert both_wrong.status_code == 422


def test_an_answer_after_the_run_finished_is_409(env: Env) -> None:
    start(env)
    decide(env, "operator")
    again = decide(env, "operator")
    assert again.status_code == 409 and body(again)["errors"][0]["code"] == "E-API-003"


def test_a_run_that_is_being_driven_refuses_a_second_operation_with_409(env: Env) -> None:
    start(env)
    locks = env.client.app.state.locks  # type: ignore[attr-defined]
    assert locks.try_acquire("r-0001")
    busy = decide(env, "operator")
    assert busy.status_code == 409 and "being driven" in body(busy)["errors"][0]["message"]
    locks.release("r-0001")
    assert decide(env, "operator").status_code == 202


def test_a_pool_that_refuses_the_job_does_not_leave_the_run_locked(tmp_path: Path) -> None:
    def refuse(job: Any) -> None:
        raise RuntimeError("the pool is shut down")

    env = published(make_env(tmp_path, submit=refuse))
    with pytest.raises(RuntimeError):
        start(env)
    assert not env.client.app.state.locks.busy("r-0001")  # type: ignore[attr-defined]


def test_a_run_that_fails_in_the_background_is_logged_and_unlocked(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    env = published(make_env(tmp_path))
    (env.config.data_dir / "BIS" / "2025Q1.xlsx").write_bytes(b"not a workbook")
    with caplog.at_level(logging.ERROR, logger="flowstat.api"):
        assert start(env).status_code == 202
    assert not env.client.app.state.locks.busy("r-0001")  # type: ignore[attr-defined]
    report = body(env.call("operator", "GET", "/runs/r-0001"))
    assert report["status"] in ("FINISHED", "WAITING")  # the engine decided; the API did not crash


# -- real threads -----------------------------------------------------------------------------
def test_several_runs_on_a_real_pool_all_reach_the_approval_without_locking_errors(
    tmp_path: Path,
) -> None:
    pool = ThreadPoolExecutor(max_workers=3)

    def submit(job: Callable[[], None]) -> None:
        pool.submit(job)

    env = published(make_env(tmp_path, submit=submit))
    ids = []
    for _ in range(6):
        response = start(env)
        assert response.status_code == 202
        ids.append(body(response)["run_id"])
    pool.shutdown(wait=True)  # joins the jobs: no sleeping in the test
    assert len(set(ids)) == 6
    for run_id in ids:
        report = body(env.call("operator", "GET", f"/runs/{run_id}"))
        assert report["status"] == "WAITING", report
    assert not any(env.client.app.state.locks.busy(r) for r in ids)  # type: ignore[attr-defined]


def test_two_answers_at_once_are_not_both_driven(tmp_path: Path) -> None:
    env = published(make_env(tmp_path))
    start(env)
    gate = threading.Barrier(2)
    results: list[int] = []

    def answer() -> None:
        gate.wait()
        results.append(decide(env, "operator").status_code)

    threads = [threading.Thread(target=answer) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [202, 409]
    report = body(env.call("operator", "GET", "/runs/r-0001"))
    assert report["status"] == "FINISHED" and report["outcome"] == "SUCCEEDED"
