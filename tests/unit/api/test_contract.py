"""The contract of the API: every route declares a permission, is denied exactly where the table of
docs/api-spec.md says, and the OpenAPI document is the golden file (NFR-08)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from flowstat.service.permissions import PERMISSIONS, PUBLIC
from tests.unit.api.helpers import NAME, Env, make_env

GOLDEN = Path(__file__).resolve().parents[2] / "golden" / "openapi-core.json"
ROLES = ("admin", "designer", "operator")
METHODS = ("get", "post", "put", "delete", "patch")


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return make_env(tmp_path)


def operations(env: Env) -> list[tuple[str, str, dict[str, Any]]]:
    paths: dict[str, dict[str, Any]] = env.client.app.openapi()["paths"]  # type: ignore[attr-defined]
    return [
        (method.upper(), path, op)
        for path, item in sorted(paths.items())
        for method, op in sorted(item.items())
        if method in METHODS
    ]


def concrete(path: str) -> str:
    """A path with its parameters filled in: names are the example workflow, numbers are 1."""
    return re.sub(r"\{(\w+)\}", lambda m: NAME if m.group(1) == "name" else "1", path)


def test_the_walk_is_not_empty(env: Env) -> None:
    found = {(m, p) for m, p, _ in operations(env)}
    assert len(found) >= 8
    assert ("POST", "/workflows/{name}/versions/{n}/publish") in found
    assert ("GET", "/health") in found


def test_every_route_declares_a_permission_that_exists(env: Env) -> None:
    for method, path, op in operations(env):
        permission = op.get("x-permission")
        assert permission is not None, f"{method} {path} has no permission"
        assert permission == PUBLIC or permission in PERMISSIONS, (method, path, permission)


def test_each_route_is_allowed_to_the_roles_of_the_table_and_to_no_other(env: Env) -> None:
    """For every route and every caller: 401 without a token, 403 where the role lacks the
    permission, anything else (404, 409, 422 ...) where it has it."""
    checked = 0
    for method, path, op in operations(env):
        permission = op["x-permission"]
        for role in (*ROLES, None):
            response = env.call(role, method, concrete(path), json={})
            if permission == PUBLIC:
                assert response.status_code == 200, (method, path, role)
            elif role is None:
                assert response.status_code == 401, (method, path)
            elif role in PERMISSIONS[permission]:
                assert response.status_code not in (401, 403), (method, path, role)
            else:
                assert response.status_code == 403, (method, path, role)
                assert response.json()["errors"][0]["code"] == "E-AUTH-002"
            checked += 1
    assert checked >= 8 * 4


def test_the_openapi_document_is_the_golden_file(env: Env) -> None:
    current = env.client.app.openapi()  # type: ignore[attr-defined]
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf8")
    assert GOLDEN.exists(), "run once with UPDATE_GOLDEN=1 to write the file"
    assert current == json.loads(GOLDEN.read_text(encoding="utf8"))
