"""Tests of the deployment service with a fake packager and a fake launcher (no process starts)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from flowstat.actions.registry import default_registry
from flowstat.compiler.catalogue import load_catalogue
from flowstat.dsl.reader import read_yaml
from flowstat.model.versions import Version
from flowstat.repo.db import open_database
from flowstat.repo.deployments import Deployment, DeploymentNotFoundError
from flowstat.repo.versions import VersionNotFoundError
from flowstat.service.deployments import (
    DeploymentFailedError,
    DeploymentService,
    Keys,
    KindUnavailableError,
    LaunchError,
    NotRunningError,
    Running,
)
from flowstat.service.runs import NotPublishedError
from flowstat.service.users import hash_token
from flowstat.service.workflows import WorkflowService
from tests.unit.engine.helpers import ROOT

T = "2026-11-08T10:00:00Z"
TEXT = (ROOT / "examples" / "quarterly_consolidation.yaml").read_text(encoding="utf8")


class FakeLauncher:
    def __init__(self, fail: str | None = None) -> None:
        self.started: list[Path] = []
        self.envs: list[dict[str, str]] = []
        self.stopped: list[str] = []
        self.fail = fail
        self.fail_stop = False

    def start(self, bundle: Path, env: Mapping[str, str] | None = None) -> Running:
        if self.fail:
            raise LaunchError("it did not start", self.fail)
        self.started.append(bundle)
        self.envs.append(dict(env or {}))
        return Running("http://127.0.0.1:9100", "pid-77")

    def stop(self, handle: str) -> None:
        if self.fail_stop:
            raise LaunchError("cannot stop", "permission denied")
        self.stopped.append(handle)


class Calls:
    def __init__(self) -> None:
        self.made: list[tuple[str, int, str, dict[str, str], str, str]] = []

    def __call__(
        self,
        root: Path,
        service_id: str,
        version: Version,
        api_key_sha256: str,
        title_keys_sha256: Mapping[str, str],
        now: str,
        kind: str,
    ) -> Path:
        self.made.append(
            (service_id, version.number, api_key_sha256, dict(title_keys_sha256), kind, str(root))
        )
        return root / service_id


def make(
    tmp_path: Path,
    launcher: FakeLauncher | None = None,
    publish: bool = True,
    ingest_url: str | None = None,
) -> tuple[DeploymentService, FakeLauncher, Calls]:
    launcher = launcher or FakeLauncher()
    calls = Calls()
    db = open_database(":memory:")
    registry = default_registry()
    workflows = WorkflowService(
        db, known_actions=registry.names(), signatures=registry.signatures()
    )
    tree, _ = read_yaml((ROOT / "examples" / "catalogue.yaml").read_text(encoding="utf8"))
    assert tree is not None
    catalogue, _ = load_catalogue(tree)
    for list_name, items in catalogue.lists.items():
        workflows.set_catalogue_list(list_name, [dict(i) for i in items])
    workflows.save_draft("quarterly_consolidation", TEXT, T)
    if publish:
        workflows.publish("quarterly_consolidation", 1, T)
    service = DeploymentService(db, workflows, calls, {"process": launcher}, tmp_path, ingest_url)
    return service, launcher, calls


def deploy(
    service: DeploymentService, kind: str = "process", number: int = 1
) -> tuple[Deployment, Keys]:
    tokens = iter(f"{n:064x}" for n in range(1, 10))
    return service.deploy(
        "quarterly_consolidation", number, kind, lambda: "d-1", lambda: next(tokens), T
    )


def test_a_published_version_is_packaged_started_and_recorded(tmp_path: Path) -> None:
    service, launcher, calls = make(tmp_path)
    row, keys = deploy(service)
    # one key to start runs, and one for the title that the approval of the example names
    assert keys.api_key == f"{1:064x}" and keys.title_keys == {"chief_statistician": f"{2:064x}"}
    assert (row.id, row.status, row.endpoint, row.handle) == (
        "d-1",
        "running",
        "http://127.0.0.1:9100",
        "pid-77",
    )
    hashes = {"chief_statistician": hash_token(keys.title_keys["chief_statistician"])}
    assert calls.made == [  # only the hashes leave this layer
        ("d-1", 1, hash_token(keys.api_key), hashes, "process", str(tmp_path))
    ]
    assert launcher.started == [tmp_path / "d-1"]
    assert service.get("d-1") == row and service.all() == [row]


def test_a_draft_a_missing_version_and_an_unknown_kind_are_refused_before_anything_is_made(
    tmp_path: Path,
) -> None:
    service, launcher, calls = make(tmp_path, publish=False)
    with pytest.raises(NotPublishedError):
        deploy(service)
    with pytest.raises(VersionNotFoundError):
        deploy(service, number=9)
    service, launcher, calls = make(tmp_path)
    with pytest.raises(ValueError, match="kind must be"):
        deploy(service, kind="vm")
    with pytest.raises(KindUnavailableError):
        deploy(service, kind="docker")  # no launcher for it in this process
    assert calls.made == [] and launcher.started == [] and service.all() == []


def test_a_service_that_does_not_start_leaves_a_failed_row_with_the_reason(tmp_path: Path) -> None:
    service, _, _ = make(tmp_path, FakeLauncher(fail="port 9100 is taken"))
    with pytest.raises(DeploymentFailedError) as info:
        deploy(service)
    assert info.value.deployment.status == "failed"
    assert info.value.deployment.detail == "port 9100 is taken"
    assert [d.status for d in service.all()] == ["failed"]


def test_a_running_deployment_is_stopped_once(tmp_path: Path) -> None:
    service, launcher, _ = make(tmp_path)
    deploy(service)
    stopped = service.stop("d-1", T)
    assert stopped.status == "stopped" and launcher.stopped == ["pid-77"]
    with pytest.raises(NotRunningError):
        service.stop("d-1", T)
    with pytest.raises(DeploymentNotFoundError):
        service.stop("nope", T)


def test_a_failed_one_cannot_be_stopped_and_a_stop_that_fails_keeps_it_running(
    tmp_path: Path,
) -> None:
    service, launcher, _ = make(tmp_path, FakeLauncher(fail="no"))
    with pytest.raises(DeploymentFailedError):
        deploy(service)
    with pytest.raises(NotRunningError):
        service.stop("d-1", T)
    service, launcher, _ = make(tmp_path)
    deploy(service)
    launcher.fail_stop = True
    with pytest.raises(LaunchError):
        service.stop("d-1", T)
    row = service.get("d-1")
    assert row.status == "running" and row.detail == "permission denied"


def test_a_third_key_is_made_and_only_its_hash_is_kept(tmp_path: Path) -> None:
    service, _, _ = make(tmp_path)
    row, keys = deploy(service)
    assert keys.ingest_key == f"{3:064x}"  # after the key of the run routes and the title keys
    assert row.ingest_key_sha256 == hash_token(keys.ingest_key)
    assert keys.ingest_key not in {keys.api_key, *keys.title_keys.values()}
    assert service.get("d-1").ingest_key_sha256 == hash_token(keys.ingest_key)


def test_the_service_gets_the_address_and_the_key_in_its_environment(tmp_path: Path) -> None:
    service, launcher, calls = make(tmp_path, ingest_url="http://127.0.0.1:8000")
    _, keys = deploy(service)
    assert launcher.envs == [
        {
            "FLOWSTAT_INGEST_URL": "http://127.0.0.1:8000",
            "FLOWSTAT_INGEST_KEY": keys.ingest_key,
            "FLOWSTAT_DEPLOYMENT_ID": "d-1",
        }
    ]
    # the packager is not given the key, so it cannot end up in the bundle
    assert keys.ingest_key not in repr(calls.made)


def test_without_an_address_the_service_gets_no_environment_and_ships_nothing(
    tmp_path: Path,
) -> None:
    service, launcher, _ = make(tmp_path)
    deploy(service)
    assert launcher.envs == [{}]
