"""Set-up of the tests of a deployed service: a real bundle made by the packager, an app over it."""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from pathlib import Path

import httpx2
from fastapi.testclient import TestClient

from flowstat.api.config import AppConfig, Submit
from flowstat.api.service import create_service_app
from flowstat.deploy.package import build_bundle
from flowstat.model.versions import Version, VersionStatus
from flowstat.service.bundle import read_bundle
from flowstat.service.users import hash_token
from tests.unit.api.helpers import EXAMPLE, NAME, REGIONS

T = "2026-11-08T10:00:00Z"
KEY = "5e" * 32


def catalogue_json() -> str:
    from flowstat.compiler.catalogue import Catalogue
    from flowstat.service.workflows import snapshot_to_json

    return snapshot_to_json(Catalogue({"regions": tuple(REGIONS)}))


def frozen(number: int = 4) -> Version:
    return Version(
        NAME,
        number,
        VersionStatus.PUBLISHED,
        EXAMPLE.replace("version: 1", f"version: {number}", 1),
        catalogue_json(),
        T,
        T,
    )


@dataclass
class ServiceEnv:
    client: TestClient
    config: AppConfig
    bundle: Path

    def call(self, key: str | None, method: str, path: str, json: object = None) -> httpx2.Response:
        headers = {} if key is None else {"Authorization": f"Bearer {key}"}
        return self.client.request(method, path, headers=headers, json=json)


def make_service(tmp_path: Path, submit: Submit | None = None, kind: str = "process") -> ServiceEnv:
    bundle = build_bundle(tmp_path / "bundles", "s-1", frozen(), hash_token(KEY), T, kind)
    ids = (f"sr-{n:04d}" for n in itertools.count(1))
    ticks = itertools.count(1)
    config = AppConfig(
        db_path=str(bundle / "service.db"),
        data_dir=bundle / "data",
        target=str(bundle / "target.db"),
        clock=lambda: T,
        time=lambda: float(next(ticks)),
        sleep=lambda seconds: None,
        new_id=lambda: next(ids),
        new_token=lambda: "k" * 64,
        submit=submit or (lambda job: job()),
        bundles=bundle,
        workers=2,
    )
    return ServiceEnv(TestClient(create_service_app(config, read_bundle(bundle))), config, bundle)
