"""Tests of the workflow routes and of the error envelope (api-spec sections 2, 4 and 6)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient

from tests.unit.api.helpers import EXAMPLE, NAME, REGIONS, Env, make_env


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return make_env(tmp_path)


def body(response: httpx2.Response) -> dict[str, Any]:
    result: dict[str, Any] = response.json()
    return result


def errors(response: httpx2.Response) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = body(response)["errors"]
    return result


# -- health and authentication ----------------------------------------------------------------
def test_health_needs_no_token(env: Env) -> None:
    response = TestClient(env.client.app).get("/health")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


@pytest.mark.parametrize("token", [None, "", "nonsense", "x" * 64])
def test_a_missing_or_unknown_token_is_401_in_the_error_envelope(
    env: Env, token: str | None
) -> None:
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    response = TestClient(env.client.app).get("/workflows", headers=headers)
    assert response.status_code == 401
    assert response.json() == {
        "errors": [{"code": "E-AUTH-001", "message": "a valid token is required"}]
    }


def test_another_scheme_than_bearer_is_401(env: Env) -> None:
    response = TestClient(env.client.app).get("/workflows", headers={"Authorization": "Basic abc"})
    assert response.status_code == 401


def test_a_disabled_user_is_401(env: Env) -> None:
    from flowstat.repo.db import open_database
    from flowstat.service.users import UserService

    db = open_database(env.config.db_path)
    UserService(db).disable("olga")
    db.close()
    assert env.call("operator", "GET", "/workflows").status_code == 401


def test_unknown_routes_and_methods_use_the_envelope(env: Env) -> None:
    missing = env.call("admin", "GET", "/nowhere")
    assert missing.status_code == 404 and errors(missing)[0]["code"] == "E-API-002"
    wrong = env.call("admin", "DELETE", "/workflows")
    assert wrong.status_code == 405 and "errors" in body(wrong)


# -- validate ---------------------------------------------------------------------------------
def test_validate_reports_errors_and_findings_without_storing(env: Env) -> None:
    ok = env.call("designer", "POST", "/validate", json={"text": EXAMPLE})
    assert ok.status_code == 200
    assert body(ok) == {"diagnostics": [], "findings": []}
    bad = env.call("designer", "POST", "/validate", json={"text": "workflow: x\nsteps: 3\n"})
    assert bad.status_code == 200
    first = body(bad)["diagnostics"][0]
    assert first["severity"] == "error" and first["line"] >= 1 and first["code"].startswith("E-")
    assert env.call("designer", "GET", "/workflows").json() == []


def test_a_body_of_the_wrong_shape_is_422_with_the_field(env: Env) -> None:
    response = env.call("designer", "POST", "/validate", json={"txt": "x"})
    assert response.status_code == 422
    found = errors(response)
    assert {e["code"] for e in found} == {"E-API-001"}
    assert {e["field"] for e in found} == {"text", "txt"}  # one is missing, one is not allowed


# -- drafts, versions, publication ------------------------------------------------------------
def test_a_draft_gets_the_next_number_and_a_draft_with_errors_is_stored(env: Env) -> None:
    first = env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    assert first.status_code == 201
    assert body(first) | {"findings": []} == {
        "workflow": NAME,
        "version": 1,
        "status": "draft",
        "diagnostics": [],
        "findings": [],
    }
    broken = env.call(
        "designer", "POST", f"/workflows/{NAME}/versions", json={"text": "version: 2\n"}
    )
    # the open draft is edited in place: it keeps its number and now holds the broken text
    assert broken.status_code == 201 and body(broken)["version"] == 1
    assert body(broken)["diagnostics"]
    stored = env.call("designer", "GET", f"/workflows/{NAME}/versions/1")
    assert body(stored)["text"] == "version: 2\n"
    assert env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish").status_code == 422
    env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    assert env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish").status_code == 200
    next_draft = env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    assert body(next_draft)["version"] == 2  # after a publication the next save is a new number


def test_publish_freezes_the_version_and_a_second_publish_is_409(env: Env) -> None:
    env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    done = env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish")
    assert done.status_code == 200
    assert body(done)["status"] == "published" and body(done)["version"] == 1
    again = env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish")
    assert again.status_code == 409 and errors(again)[0]["code"] == "E-API-003"


def test_publish_of_a_text_with_errors_is_422_with_the_diagnostics(env: Env) -> None:
    wrong_number = EXAMPLE.replace("version: 1", "version: 7")
    env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": wrong_number})
    response = env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish")
    assert response.status_code == 422
    found = errors(response)
    assert found[0]["code"] == "E-API-004"
    detail = next(e for e in found if e["code"] == "E-VER-001")
    assert detail["line"] == 3 and "version 7" in str(detail["message"])


def test_unknown_workflows_and_versions_are_404(env: Env) -> None:
    assert env.call("designer", "POST", "/workflows/none/versions/1/publish").status_code == 404
    assert env.call("designer", "GET", "/workflows/none/versions/1").status_code == 404
    env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    assert env.call("designer", "GET", f"/workflows/{NAME}/versions/9").status_code == 404


def test_lists_and_reads(env: Env) -> None:
    env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish")
    env.call(
        "designer",
        "POST",
        f"/workflows/{NAME}/versions",
        json={"text": EXAMPLE.replace("version: 1", "version: 2")},
    )
    listing = env.call("operator", "GET", "/workflows")
    assert listing.json() == [{"name": NAME, "latest": 2, "latest_published": 1}]
    versions = env.call("operator", "GET", f"/workflows/{NAME}/versions").json()
    assert [(v["number"], v["status"]) for v in versions] == [(1, "published"), (2, "draft")]
    assert versions[0]["published_at"] and versions[1]["published_at"] is None
    one = env.call("operator", "GET", f"/workflows/{NAME}/versions/1").json()
    assert one["text"] == EXAMPLE and one["status"] == "published" and one["findings"] == []


# -- catalogue --------------------------------------------------------------------------------
def test_the_catalogue_is_replaced_by_an_admin_and_used_by_the_next_draft(tmp_path: Path) -> None:
    env = make_env(tmp_path, with_catalogue=False)
    before = env.call("designer", "POST", "/validate", json={"text": EXAMPLE})
    assert body(before)["diagnostics"]  # no regions: the foreach has nothing to expand
    put = env.call("admin", "PUT", "/catalogue/regions", json={"items": REGIONS})
    assert put.status_code == 200
    after = env.call("designer", "POST", "/validate", json={"text": EXAMPLE})
    assert body(after) == {"diagnostics": [], "findings": []}


def test_a_catalogue_with_a_repeated_code_is_422(env: Env) -> None:
    items = [{"code": "A", "name": "a"}, {"code": "A", "name": "b"}]
    response = env.call("admin", "PUT", "/catalogue/regions", json={"items": items})
    assert response.status_code == 422 and errors(response)[0]["code"] == "E-API-001"
