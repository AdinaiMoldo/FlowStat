"""The core takes the events of a service: once, in order, from the right deployment (US-31)."""

from __future__ import annotations

import json
import sqlite3

import pytest

from flowstat.model.events import Event, EventKind, event_to_dict
from flowstat.repo.db import open_database
from flowstat.repo.deployments import Deployment, DeploymentStore
from flowstat.repo.runs import RunStore
from flowstat.service.ingest import (
    MAX_BATCH,
    Batch,
    IngestConflictError,
    IngestInvalidError,
    IngestService,
    RunMeta,
    UnknownKeyError,
    WrongDeploymentError,
    parse_batch,
)
from flowstat.service.users import hash_token

T = "2026-11-09T10:00:00Z"
LATER = "2026-11-09T10:05:00Z"
KEY, OTHER_KEY = "k1" * 32, "k2" * 32


def core() -> tuple[sqlite3.Connection, IngestService]:
    db = open_database(":memory:")
    store = DeploymentStore(db)
    for name, key in (("d-1", KEY), ("d-2", OTHER_KEY)):
        store.add(
            Deployment(
                name, "demo", 4, "process", "running", "/b", T, T, ingest_key_sha256=hash_token(key)
            )
        )
    return db, IngestService(db)


def history(n: int) -> list[Event]:
    return [Event(i, EventKind.STEP_STARTED, float(i), step="a") for i in range(n)]


def batch(run: str = "r1", n: int = 3, start: int = 0, version: int = 4) -> Batch:
    meta = RunMeta(run, "demo", version, {"quarter": "2025Q1"}, T)
    return Batch(meta, tuple(history(start + n)[start:]))


# -- who may ship ---------------------------------------------------------------------------
def test_the_key_of_the_deployment_is_accepted() -> None:
    _, ingest = core()
    assert ingest.authenticate("d-1", KEY).id == "d-1"


def test_an_unknown_key_is_refused_as_unknown() -> None:
    _, ingest = core()
    for token in ("", "nonsense", "k3" * 32):
        with pytest.raises(UnknownKeyError):
            ingest.authenticate("d-1", token)


def test_the_key_of_another_deployment_is_refused_as_wrong() -> None:
    _, ingest = core()
    with pytest.raises(WrongDeploymentError):
        ingest.authenticate("d-1", OTHER_KEY)
    with pytest.raises(WrongDeploymentError):
        ingest.authenticate("d-9", KEY)  # there is no such deployment: not told apart


def test_a_deployment_without_a_key_takes_no_events() -> None:
    db, ingest = core()
    DeploymentStore(db).add(Deployment("d-3", "demo", 4, "process", "running", "/b", T, T))
    with pytest.raises(UnknownKeyError):
        ingest.authenticate("d-3", "")


# -- what is stored ---------------------------------------------------------------------------
def test_a_batch_makes_the_run_with_its_origin_and_stores_the_events() -> None:
    db, ingest = core()
    stored = ingest.accept(ingest.authenticate("d-1", KEY), batch(), LATER)
    assert stored == 2
    runs = RunStore(db)
    run = runs.get("r1")
    assert (run.origin, run.workflow, run.version, run.inputs) == (
        "d-1",
        "demo",
        4,
        {"quarter": "2025Q1"},
    )
    assert [e.seq for e in runs.events("r1")] == [0, 1, 2]
    assert DeploymentStore(db).get("d-1").last_event_at == LATER


def test_the_same_batch_twice_leaves_no_duplicates() -> None:
    db, ingest = core()
    deployment = ingest.authenticate("d-1", KEY)
    assert ingest.accept(deployment, batch(), T) == 2
    assert ingest.accept(deployment, batch(), LATER) == 2
    assert [e.seq for e in RunStore(db).events("r1")] == [0, 1, 2]


def test_a_longer_batch_that_repeats_the_start_adds_only_the_new_events() -> None:
    db, ingest = core()
    deployment = ingest.authenticate("d-1", KEY)
    ingest.accept(deployment, batch(n=3), T)
    assert ingest.accept(deployment, batch(n=5), LATER) == 4
    assert [e.seq for e in RunStore(db).events("r1")] == [0, 1, 2, 3, 4]


def test_a_gap_stores_nothing_and_says_what_is_needed() -> None:
    db, ingest = core()
    deployment = ingest.authenticate("d-1", KEY)
    ingest.accept(deployment, batch(n=3), T)
    with pytest.raises(IngestConflictError) as gap:
        ingest.accept(deployment, batch(n=2, start=5), LATER)
    assert gap.value.expected == 3
    assert len(RunStore(db).events("r1")) == 3
    assert DeploymentStore(db).get("d-1").last_event_at == T  # a refused batch is not an event


def test_an_event_that_differs_from_the_stored_one_is_a_conflict_without_a_number() -> None:
    _, ingest = core()
    deployment = ingest.authenticate("d-1", KEY)
    ingest.accept(deployment, batch(n=3), T)
    changed = Batch(batch().run, (Event(0, EventKind.STEP_STARTED, 0.0, step="other"),))
    with pytest.raises(IngestConflictError) as conflict:
        ingest.accept(deployment, changed, LATER)
    assert conflict.value.expected is None


def test_a_run_of_the_core_with_the_same_id_is_not_overwritten() -> None:
    db, ingest = core()
    RunStore(db).create("r1", "demo", 4, {"quarter": "2025Q1"}, T)
    with pytest.raises(IngestConflictError):
        ingest.accept(ingest.authenticate("d-1", KEY), batch(), LATER)
    assert RunStore(db).events("r1") == []


def test_two_deployments_may_not_claim_one_run_id() -> None:
    _, ingest = core()
    ingest.accept(ingest.authenticate("d-1", KEY), batch(), T)
    with pytest.raises(IngestConflictError):
        ingest.accept(ingest.authenticate("d-2", OTHER_KEY), batch(), LATER)


# -- what is refused as invalid ----------------------------------------------------------------
@pytest.mark.parametrize(
    "meta",
    [RunMeta("r1", "other", 4, {}, T), RunMeta("r1", "demo", 5, {}, T)],
    ids=["workflow", "version"],
)
def test_a_run_of_another_workflow_or_version_than_the_deployment_is_invalid(meta: RunMeta) -> None:
    db, ingest = core()
    with pytest.raises(IngestInvalidError):
        ingest.accept(ingest.authenticate("d-1", KEY), Batch(meta, tuple(history(1))), T)
    assert RunStore(db).runs() == []


def test_an_empty_batch_and_a_batch_that_is_too_big_are_invalid() -> None:
    _, ingest = core()
    deployment = ingest.authenticate("d-1", KEY)
    with pytest.raises(IngestInvalidError, match="at least one"):
        ingest.accept(deployment, Batch(batch().run, ()), T)
    with pytest.raises(IngestInvalidError, match=str(MAX_BATCH)):
        ingest.accept(deployment, batch(n=MAX_BATCH + 1), T)
    assert ingest.accept(deployment, batch(n=MAX_BATCH), T) == MAX_BATCH - 1


# -- the form on the wire ----------------------------------------------------------------------
def test_a_batch_is_read_from_plain_data_and_back() -> None:
    original = batch(n=2)
    data = {
        "run": {
            "run_id": "r1",
            "workflow": "demo",
            "version": 4,
            "inputs": {"quarter": "2025Q1"},
            "created_at": T,
        },
        "events": [event_to_dict(e) for e in original.events],
    }
    assert parse_batch(json.loads(json.dumps(data))) == original


@pytest.mark.parametrize(
    "broken",
    [
        {},
        {"run": {}, "events": []},
        {"run": {"run_id": "r", "workflow": "w", "version": 1, "inputs": {}, "created_at": T}},
        {
            "run": {"run_id": "r", "workflow": "w", "version": 1, "inputs": [], "created_at": T},
            "events": [],
        },
        {
            "run": {"run_id": "r", "workflow": "w", "version": "1", "inputs": {}, "created_at": T},
            "events": [],
        },
        {
            "run": {"run_id": "r", "workflow": "w", "version": 1, "inputs": {}, "created_at": T},
            "events": [{"seq": 0}],
        },
    ],
)
def test_data_that_is_not_a_batch_is_refused(broken: dict[str, object]) -> None:
    with pytest.raises(IngestInvalidError):
        parse_batch(broken)
