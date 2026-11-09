"""POST /complete (US-27): the route over the completion of the service."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.api.helpers import EXAMPLE, Env, make_env


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return make_env(tmp_path)


def cursor(text: str, step: str) -> tuple[str, int, int]:
    """The example with a reference opened in the `with` of ``step``; and the cursor in it."""
    lines = text.split("\n")
    start = next(i for i, x in enumerate(lines) if x.startswith(f"  {step}:"))
    row = next(i for i in range(start, len(lines)) if lines[i].lstrip().startswith("with:"))
    lines[row] = '    with: {x: "${}"}'
    return "\n".join(lines), row + 1, lines[row].index("${") + 3


def test_it_offers_what_is_available_at_the_step(env: Env) -> None:
    text, line, column = cursor(EXAMPLE, "approve")
    reply = env.call(
        "designer", "POST", "/complete", {"text": text, "line": line, "column": column}
    )
    assert reply.status_code == 200
    body = reply.json()
    labels = [i["label"] for i in body["items"]]
    assert labels[0] == "inputs.quarter" and "consolidate.rows" in labels
    assert not any(x.startswith(("publish", "approve")) for x in labels)
    assert body["prefix"] == "" and body["note"] is None
    assert body["from_column"] == column
    assert body["closed"] is True
    assert {"label": "inputs.quarter", "kind": "input", "detail": "input, string"} in body["items"]


def test_a_cursor_outside_a_reference_gets_a_note(env: Env) -> None:
    reply = env.call("designer", "POST", "/complete", {"text": EXAMPLE, "line": 3, "column": 2})
    assert reply.status_code == 200
    assert reply.json()["items"] == [] and "${" in reply.json()["note"]


def test_a_position_that_is_not_a_position_is_422(env: Env) -> None:
    for body in (
        {"text": "x", "line": 0, "column": 1},
        {"text": "x", "line": 1, "column": 0},
        {"text": "x", "line": "a", "column": 1},
        {"text": "x", "line": 1},
    ):
        reply = env.call("designer", "POST", "/complete", body)
        assert reply.status_code == 422, body
        assert reply.json()["errors"][0]["code"] == "E-API-001"


def test_only_those_who_write_workflows_may_ask(env: Env) -> None:
    body = {"text": EXAMPLE, "line": 1, "column": 1}
    assert env.call("operator", "POST", "/complete", body).status_code == 403
    assert env.call(None, "POST", "/complete", body).status_code == 401
    assert env.call("admin", "POST", "/complete", body).status_code == 200
