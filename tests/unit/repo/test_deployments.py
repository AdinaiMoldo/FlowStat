"""Tests of the registry of deployments (A6)."""

from __future__ import annotations

import sqlite3

import pytest

from flowstat.repo.db import open_database
from flowstat.repo.deployments import Deployment, DeploymentNotFoundError, DeploymentStore

T0, T1, T2 = "2026-11-08T10:00:00Z", "2026-11-08T10:01:00Z", "2026-11-08T10:02:00Z"


def row(deployment_id: str = "d-1", created: str = T0, kind: str = "process") -> Deployment:
    return Deployment(
        deployment_id, "demo", 4, kind, "starting", "/b/" + deployment_id, created, created
    )


def test_a_row_is_stored_and_read_back() -> None:
    store = DeploymentStore(open_database(":memory:"))
    store.add(row())
    assert store.get("d-1") == row()


def test_the_state_changes_and_what_is_not_given_is_kept() -> None:
    store = DeploymentStore(open_database(":memory:"))
    store.add(row())
    running = store.update("d-1", T1, status="running", endpoint="http://127.0.0.1:9", handle="42")
    assert running.status == "running" and running.updated_at == T1
    stopped = store.update("d-1", T2, status="stopped")
    assert stopped.endpoint == "http://127.0.0.1:9" and stopped.handle == "42"
    assert store.get("d-1") == stopped
    failed = store.update("d-1", T2, status="failed", detail="the port is taken")
    assert failed.detail == "the port is taken"


def test_the_rows_come_newest_first() -> None:
    store = DeploymentStore(open_database(":memory:"))
    store.add(row("d-1", T0))
    store.add(row("d-2", T1))
    assert [d.id for d in store.all()] == ["d-2", "d-1"]


def test_an_unknown_row_is_an_error() -> None:
    store = DeploymentStore(open_database(":memory:"))
    with pytest.raises(DeploymentNotFoundError):
        store.get("nope")
    with pytest.raises(DeploymentNotFoundError):
        store.update("nope", T1, status="running")


def test_a_kind_or_status_the_registry_does_not_know_is_refused_by_the_database() -> None:
    store = DeploymentStore(open_database(":memory:"))
    with pytest.raises(sqlite3.IntegrityError):
        store.add(row(kind="vm"))
    store.add(row())
    with pytest.raises(sqlite3.IntegrityError):
        store.update("d-1", T1, status="paused")
