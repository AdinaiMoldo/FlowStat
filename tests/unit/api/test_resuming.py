"""What a core or a service does with the runs the last process left going (US-36)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from flowstat.actions.registry import default_registry
from flowstat.api.locks import RunLocks
from flowstat.api.resuming import resume_on_start
from flowstat.model.events import Event, EventKind
from flowstat.repo.db import connect
from flowstat.repo.runs import RunStore
from tests.unit.api.helpers import T, make_env


def stored_run(path: str, run_id: str = "r-x") -> None:
    db = connect(path)
    store = RunStore(db)
    store.create(run_id, "nothing", 1, {}, T)
    store.append(run_id, [Event(0, EventKind.RUN_STARTED, 0.0)])
    db.close()


def test_a_run_that_another_thread_already_drives_is_left_to_it(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    stored_run(env.config.db_path)
    locks = RunLocks()
    assert locks.try_acquire("r-x")
    assert resume_on_start(env.config, default_registry(), locks) == []
    assert locks.busy("r-x")  # the lock is not ours to release


def test_a_pool_that_refuses_the_job_does_not_stop_the_start(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from dataclasses import replace

    env = make_env(tmp_path)
    stored_run(env.config.db_path)
    stored_run(env.config.db_path, "r-y")

    def refuse(job: object) -> None:
        raise RuntimeError("the pool is shut")

    config = replace(env.config, submit=refuse)
    locks = RunLocks()
    with caplog.at_level(logging.ERROR, logger="flowstat.api"):
        assert resume_on_start(config, default_registry(), locks) == []
    assert "r-x could not be taken up" in caplog.text and "r-y could not be taken up" in caplog.text
    assert not locks.busy("r-x") and not locks.busy("r-y")


def test_a_database_that_cannot_be_read_is_reported_and_the_start_goes_on(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from dataclasses import replace

    env = make_env(tmp_path)
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"this is not a database" * 100)
    config = replace(env.config, db_path=str(broken))
    with caplog.at_level(logging.ERROR, logger="flowstat.api"):
        assert resume_on_start(config, default_registry(), RunLocks()) == []
    assert "could not be listed" in caplog.text


def test_a_run_whose_version_is_gone_stops_with_an_error_and_frees_its_lock(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    env = make_env(tmp_path)
    stored_run(env.config.db_path)  # a run of a workflow that has no version here
    locks = RunLocks()
    with caplog.at_level(logging.ERROR, logger="flowstat.api"):
        assert resume_on_start(env.config, default_registry(), locks) == ["r-x"]
    assert "stopped with an error" in caplog.text and not locks.busy("r-x")


def test_a_service_that_starts_takes_up_the_run_it_left_going(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from flowstat.api.service import create_service_app
    from flowstat.service.bundle import read_bundle
    from tests.unit.actions.helpers import write_all_forms
    from tests.unit.api.service_helpers import KEY, make_service

    (tmp_path / "whole").mkdir()
    (tmp_path / "cut").mkdir()
    whole = make_service(tmp_path / "whole")
    write_all_forms(whole.config.data_dir, "2025Q1")
    started = whole.call(KEY, "POST", "/runs", {"inputs": {"quarter": "2025Q1"}})
    assert started.status_code == 202
    run_id = started.json()["run_id"]
    expected = whole.call(KEY, "GET", f"/runs/{run_id}").json()
    assert expected["status"] == "WAITING"
    db = connect(whole.config.db_path)
    history = RunStore(db).events(run_id)
    db.close()

    cut = make_service(tmp_path / "cut")
    write_all_forms(cut.config.data_dir, "2025Q1")
    db = connect(cut.config.db_path)  # the process stopped after the fifth event
    store = RunStore(db)
    store.create(run_id, expected["workflow"], expected["version"], {"quarter": "2025Q1"}, T)
    store.append(run_id, history[:5])
    db.close()
    restarted = TestClient(create_service_app(cut.config, read_bundle(cut.bundle)))
    after = restarted.get(f"/runs/{run_id}", headers={"Authorization": f"Bearer {KEY}"}).json()
    assert after["status"] == "WAITING"
    assert {s["step"]: s["state"] for s in after["steps"]} == {
        s["step"]: s["state"] for s in expected["steps"]
    }
