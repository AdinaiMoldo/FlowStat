"""Acceptance test of US-29: an admin restricts publishing to designers.

Given a user with the operator role, when they publish, then the API returns 403.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.api.helpers import EXAMPLE, NAME, make_env


def test_an_operator_who_publishes_gets_403_and_nothing_is_published(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    saved = env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    assert saved.status_code == 201

    refused = env.call("operator", "POST", f"/workflows/{NAME}/versions/1/publish")
    assert refused.status_code == 403
    assert refused.json() == {
        "errors": [
            {
                "code": "E-AUTH-002",
                "message": "the role 'operator' may not do this (workflows:publish)",
            }
        ]
    }
    versions = env.call("designer", "GET", f"/workflows/{NAME}/versions").json()
    assert [v["status"] for v in versions] == ["draft"]  # the refusal changed nothing

    # the designer, and the admin who restricts it to designers, can
    assert env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish").status_code == 200


def test_an_admin_can_publish_too(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    assert env.call("admin", "POST", f"/workflows/{NAME}/versions/1/publish").status_code == 200


def test_a_call_without_a_token_is_401_and_the_operator_still_reads(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    assert env.call(None, "POST", f"/workflows/{NAME}/versions/1/publish").status_code == 401
    assert env.call("operator", "GET", "/workflows").status_code == 200
