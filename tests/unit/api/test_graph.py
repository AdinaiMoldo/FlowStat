"""The graph routes (US-28): a text without storing it, and a stored version."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.api.helpers import EXAMPLE, NAME, Env, make_env

BAD = EXAMPLE.replace("[publish, return_to_regions]", "[publish, return_to_region]")


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return make_env(tmp_path)


def test_a_valid_text_gives_its_graph_and_no_diagnostics(env: Env) -> None:
    reply = env.call("designer", "POST", "/graph", {"text": EXAMPLE})
    assert reply.status_code == 200
    body = reply.json()
    assert body["diagnostics"] == []
    graph = body["graph"]
    assert len(graph["nodes"]) == 22 and len(graph["edges"]) == 21
    assert graph["groups"] == [{"id": "load", "size": 9}, {"id": "check", "size": 9}]
    publish = next(n for n in graph["nodes"] if n["id"] == "publish")
    assert publish["conditional"] and publish["goal"] and publish["layer"] == 4
    assert {"source": "approve", "target": "publish"} in graph["edges"]


def test_a_text_with_errors_gives_the_errors_and_no_graph(env: Env) -> None:
    reply = env.call("designer", "POST", "/graph", {"text": BAD})
    assert reply.status_code == 200
    body = reply.json()
    assert body["graph"] is None
    assert [d["code"] for d in body["diagnostics"]] == ["E-REF-001"]
    assert body["diagnostics"][0]["line"] == 38


def test_the_preview_stores_nothing(env: Env) -> None:
    env.call("designer", "POST", "/graph", {"text": EXAMPLE})
    assert env.call("designer", "GET", "/workflows").json() == []


def test_a_stored_version_has_the_same_graph_as_its_text(env: Env) -> None:
    from_text = env.call("designer", "POST", "/graph", {"text": EXAMPLE}).json()["graph"]
    env.call("designer", "POST", f"/workflows/{NAME}/versions", {"text": EXAMPLE})
    stored = env.call("operator", "GET", f"/workflows/{NAME}/versions/1/graph")
    assert stored.status_code == 200
    assert stored.json() == {"graph": from_text}


def test_an_unknown_version_is_404(env: Env) -> None:
    reply = env.call("designer", "GET", f"/workflows/{NAME}/versions/7/graph")
    assert reply.status_code == 404
    assert reply.json()["errors"][0]["code"] == "E-API-002"


def test_a_stored_draft_with_errors_has_no_graph_and_says_why(env: Env) -> None:
    env.call("designer", "POST", f"/workflows/{NAME}/versions", {"text": BAD})
    reply = env.call("designer", "GET", f"/workflows/{NAME}/versions/1/graph")
    assert reply.status_code == 422
    codes = [e["code"] for e in reply.json()["errors"]]
    assert codes == ["E-API-004", "E-REF-001"]


def test_an_operator_may_read_a_graph_but_not_preview_a_text(env: Env) -> None:
    assert env.call("operator", "POST", "/graph", {"text": EXAMPLE}).status_code == 403
    assert env.call(None, "POST", "/graph", {"text": EXAMPLE}).status_code == 401
    env.call("designer", "POST", f"/workflows/{NAME}/versions", {"text": EXAMPLE})
    assert env.call("operator", "GET", f"/workflows/{NAME}/versions/1/graph").status_code == 200
