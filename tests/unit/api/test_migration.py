"""The check of which runs can move to another version (US-32): the route and its permissions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.unit.actions.helpers import write_all_forms
from tests.unit.api.helpers import EXAMPLE, NAME, Env, make_env

INPUTS = {"inputs": {"quarter": "2025Q1"}}
NOTIFY = (
    "  notify:\n    action: email.send\n    needs: [publish]\n    with: {to: ops}\n"
    "goals: {any_of: [publish, return_to_regions]}"
)


def save(env: Env, text: str, publish: bool = True) -> None:
    listing = env.call("designer", "GET", f"/workflows/{NAME}/versions")
    number = len(listing.json()) + 1 if listing.status_code == 200 else 1
    text = text.replace("version: 1", f"version: {number}", 1)
    response = env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": text})
    assert response.status_code == 201, response.text
    if publish:
        url = f"/workflows/{NAME}/versions/{number}/publish"
        assert env.call("designer", "POST", url).status_code == 200


@pytest.fixture
def env(tmp_path: Path) -> Env:
    made = make_env(tmp_path)
    save(made, EXAMPLE)
    write_all_forms(made.config.data_dir, "2025Q1")
    assert made.call("operator", "POST", f"/workflows/{NAME}/runs", json=INPUTS).status_code == 202
    return made


def ask(env: Env, source: int, target: int, role: str = "operator") -> Any:
    return env.call(role, "GET", f"/workflows/{NAME}/migration?from={source}&to={target}")


def test_a_waiting_run_can_move_to_a_version_that_adds_a_step_and_changes_a_later_one(
    env: Env,
) -> None:
    changed = EXAMPLE.replace("retry: {max: 3,", "retry: {max: 5,").replace(
        "goals: {any_of: [publish, return_to_regions]}", NOTIFY
    )
    save(env, changed)
    response = ask(env, 1, 2)
    assert response.status_code == 200
    answer = response.json()
    assert (answer["workflow"], answer["from"], answer["to"]) == (NAME, 1, 2)
    [run] = answer["runs"]
    assert (run["run_id"], run["owner"], run["status"]) == ("r-0001", "core", "WAITING")
    assert run["migratable"] is True and run["reasons"] == []
    assert run["added"] == ["notify"]
    assert run["pending"] == ["publish", "return_to_regions", "notify"]
    assert "approve" in run["kept"] and run["dropped"] == []


def test_a_change_of_a_step_that_ran_is_refused_with_its_reason(env: Env) -> None:
    save(
        env,
        EXAMPLE.replace(
            '    with: {parts: "${check[*].rows}"}\n',
            '    with: {parts: "${check[*].rows}"}\n    ensures: "len(${consolidate.rows}) > 0"\n',
        ),
    )
    [run] = ask(env, 1, 2).json()["runs"]
    assert run["migratable"] is False
    assert [(r["code"], r["step"]) for r in run["reasons"]] == [("E-MIG-004", "consolidate")]
    assert "ensures" in run["reasons"][0]["message"]


def test_runs_of_other_versions_and_finished_runs_are_not_listed(env: Env) -> None:
    save(env, EXAMPLE)
    assert ask(env, 2, 1).json()["runs"] == []
    env.call(
        "operator",
        "POST",
        "/runs/r-0001/decisions",
        json={"step": "approve", "decision": "approved"},
    )
    assert ask(env, 1, 2).json()["runs"] == []


def test_the_versions_must_exist_be_published_and_differ(env: Env) -> None:
    assert ask(env, 1, 1).status_code == 422
    assert ask(env, 1, 9).status_code == 404
    save(env, EXAMPLE, publish=False)
    assert ask(env, 1, 2).status_code == 409
    assert ask(env, 1, 2).json()["errors"][0]["code"] == "E-API-003"


@pytest.mark.parametrize(("role", "status"), [("operator", 200), ("designer", 200), ("admin", 200)])
def test_every_role_that_reads_runs_may_ask(env: Env, role: str, status: int) -> None:
    save(env, EXAMPLE)
    assert ask(env, 1, 2, role).status_code == status


def test_no_token_is_401(env: Env) -> None:
    save(env, EXAMPLE)
    assert ask(env, 1, 2, role=None).status_code == 401  # type: ignore[arg-type]
