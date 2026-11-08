"""Shared set-up of the API tests: a database file, one user of each role, a test client."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx2
from fastapi.testclient import TestClient

from flowstat.api.app import create_app
from flowstat.api.config import AppConfig
from flowstat.repo.db import open_database
from flowstat.service.users import UserService

ROOT = Path(__file__).resolve().parents[3]
T = "2026-11-08T10:00:00Z"
EXAMPLE = (ROOT / "examples" / "quarterly_consolidation.yaml").read_text(encoding="utf8")
REGIONS = [
    {"code": c, "name": c.title()}
    for c in ("BIS", "OSC", "BAT", "JAL", "ISK", "NAR", "OSH", "TAL", "CHU")
]
TOKENS = {
    "admin": "a1" * 32,
    "designer": "d2" * 32,
    "operator": "o3" * 32,
}
NAME = "quarterly_consolidation"


def headers(role: str | None) -> dict[str, str]:
    return {} if role is None else {"Authorization": f"Bearer {TOKENS[role]}"}


@dataclass
class Env:
    client: TestClient
    config: AppConfig

    def call(
        self, role: str | None, method: str, path: str, json: object = None
    ) -> httpx2.Response:
        return self.client.request(method, path, headers=headers(role), json=json)


def make_env(tmp_path: Path, with_catalogue: bool = True) -> Env:
    path = str(tmp_path / "flowstat.db")
    db = open_database(path)
    users = UserService(db)
    users.add("ada", "admin", set(), TOKENS["admin"], T)
    users.add("dina", "designer", set(), TOKENS["designer"], T)
    users.add("olga", "operator", {"chief_statistician"}, TOKENS["operator"], T)
    if with_catalogue:
        from flowstat.service.workflows import WorkflowService

        WorkflowService(db).set_catalogue_list("regions", REGIONS)
    db.close()
    config = AppConfig(db_path=path, data_dir=tmp_path / "data", clock=lambda: T)
    return Env(TestClient(create_app(config)), config)
