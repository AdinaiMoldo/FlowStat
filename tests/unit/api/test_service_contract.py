"""The contract of a deployed service: a declared permission on every route, only the key of the
service gets in, and the OpenAPI document is the golden file."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from flowstat.service.permissions import PERMISSIONS, PUBLIC
from tests.unit.api.helpers import NAME
from tests.unit.api.service_helpers import KEY, ServiceEnv, make_service

GOLDEN = Path(__file__).resolve().parents[2] / "golden" / "openapi-service.json"
METHODS = ("get", "post", "put", "delete", "patch")


@pytest.fixture
def svc(tmp_path: Path) -> ServiceEnv:
    return make_service(tmp_path)


def operations(svc: ServiceEnv) -> list[tuple[str, str, dict[str, Any]]]:
    paths: dict[str, dict[str, Any]] = svc.client.app.openapi()["paths"]  # type: ignore[attr-defined]
    return [
        (m.upper(), p, op)
        for p, item in sorted(paths.items())
        for m, op in sorted(item.items())
        if m in METHODS
    ]


def test_the_service_has_the_run_routes_and_nothing_about_workflows(svc: ServiceEnv) -> None:
    found = {(m, p) for m, p, _ in operations(svc)}
    assert found == {
        ("GET", "/health"),
        ("GET", "/schema"),
        ("POST", "/runs"),
        ("GET", "/runs"),
        ("GET", "/runs/{run_id}"),
        ("POST", "/runs/{run_id}/decisions"),
        ("GET", "/migration/{run_id}/ownership"),
        ("GET", "/migration/{run_id}/export"),
        ("POST", "/migration/{run_id}/fence"),
        ("POST", "/migration/{run_id}/adopt"),
        ("POST", "/migration/{run_id}/release"),
    }


def test_every_route_declares_a_permission_and_the_key_has_it_exactly_where_the_table_says(
    svc: ServiceEnv,
) -> None:
    """The key of a service is an operator: it may do what the table gives the operator, and the
    routes of the handover, which the table gives to an admin only, are closed to it."""
    for method, path, op in operations(svc):
        permission = op["x-permission"]
        assert permission == PUBLIC or permission in PERMISSIONS, (method, path)
        concrete = re.sub(r"\{(\w+)\}", "x", path)
        without = svc.call(None, method, concrete, json={})
        with_key = svc.call(KEY, method, concrete, json={})
        if permission == PUBLIC:
            assert without.status_code == 200 and with_key.status_code == 200
        else:
            assert without.status_code == 401, (method, path)
            if "operator" in PERMISSIONS[permission]:
                assert with_key.status_code not in (401, 403), (method, path)
            else:
                assert permission == "handover:control", (method, path)
                assert with_key.status_code == 403, (method, path)


def test_the_openapi_document_is_the_golden_file(svc: ServiceEnv) -> None:
    current = svc.client.app.openapi()  # type: ignore[attr-defined]
    current["info"]["title"] = (
        "FlowStat service"  # the title names the workflow; the shape is fixed
    )
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf8")
    assert GOLDEN.exists(), "run once with UPDATE_GOLDEN=1 to write the file"
    assert current == json.loads(GOLDEN.read_text(encoding="utf8"))
    assert NAME not in json.dumps(current)  # nothing in the contract depends on the workflow
