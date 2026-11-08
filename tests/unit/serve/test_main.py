"""Tests of the executable that starts the API: its configuration and its stop."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi import FastAPI

from flowstat.api.edge import RunPool
from flowstat.repo.db import open_database
from flowstat.serve.__main__ import main
from flowstat.service.users import UserService
from tests.unit.api.helpers import TOKENS

T = "2026-11-08T10:00:00Z"


def test_main_makes_the_tables_builds_the_app_and_serves_it(tmp_path: Path) -> None:
    db = str(tmp_path / "flowstat.db")
    served: list[tuple[FastAPI, str, int]] = []
    code = main(
        ["--db", db, "--data", str(tmp_path), "--target", str(tmp_path / "t.db"), "--port", "9001"],
        serve=lambda app, host, port: served.append((app, host, port)),
    )
    assert code == 0
    [(app, host, port)] = served
    assert (host, port) == ("127.0.0.1", 9001) and app.title == "FlowStat"
    UserService(open_database(db)).add("ada", "admin", set(), TOKENS["admin"], T)  # tables exist


def test_the_identifiers_are_random_and_the_clock_is_real(tmp_path: Path) -> None:
    configs = []
    main(
        ["--db", str(tmp_path / "f.db"), "--data", str(tmp_path), "--target", "t.db"],
        serve=lambda app, host, port: configs.append(app.state.config),
    )
    config = configs[0]
    assert config.new_id() != config.new_id() and len(config.new_id()) == 12
    assert config.clock().endswith("Z") and config.time() > 1_700_000_000


def test_the_pool_waits_for_runs_in_flight_and_reports_the_ones_left() -> None:
    pool = RunPool(2)
    release = threading.Event()
    done = threading.Event()
    pool.submit(done.set)

    def hold() -> None:
        release.wait(10)

    pool.submit(hold)  # a run that does not finish
    assert done.wait(5)
    assert pool.stop(0.05) == 1
    release.set()


def test_the_pool_has_nothing_left_when_every_run_finished() -> None:
    pool = RunPool(1)
    done = threading.Event()
    pool.submit(done.set)
    assert done.wait(5)
    assert pool.stop(2.0) == 0


def test_a_message_names_the_runs_left_at_stop(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    release = threading.Event()

    def serve(app: FastAPI, host: str, port: int) -> None:
        def hold() -> None:
            release.wait(10)

        app.state.config.submit(hold)

    main(
        ["--db", str(tmp_path / "f.db"), "--data", str(tmp_path), "--target", "t.db",
         "--stop-limit", "0.05"],
        serve=serve,
    )  # fmt: skip
    release.set()
    assert "1 run(s) were still going" in capsys.readouterr().err
