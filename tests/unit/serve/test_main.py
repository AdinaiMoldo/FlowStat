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


def test_services_are_told_to_reach_the_core_at_its_own_address_unless_said_otherwise(
    tmp_path: Path,
) -> None:
    def ingest_url(*extra: str) -> str | None:
        seen: list[str | None] = []
        main(
            ["--db", str(tmp_path / "f.db"), "--data", str(tmp_path), "--target", "t.db", *extra],
            serve=lambda app, host, port: seen.append(app.state.config.ingest_url),
        )
        return seen[0]

    assert ingest_url("--port", "9001") == "http://127.0.0.1:9001"
    assert ingest_url("--host", "0.0.0.0", "--port", "9002") == "http://127.0.0.1:9002"
    assert ingest_url("--ingest-url", "http://core.office:8000") == "http://core.office:8000"


# -- the hosts http.call may reach (US-39) ---------------------------------------------------------
def served_config(tmp_path: Path, *extra: str) -> object:
    configs = []
    main(
        ["--db", str(tmp_path / "f.db"), "--data", str(tmp_path), "--target", "t.db", *extra],
        serve=lambda app, host, port: configs.append(app.state.config),
    )
    return configs[0]


def test_hosts_are_given_once_each_and_reach_the_registry_and_the_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FLOWSTAT_ALLOWED_HOSTS", raising=False)
    config = served_config(tmp_path, "--allow-host", "Api.Example.kg", "--allow-host", "b.kg")
    assert config.allowed_hosts == frozenset({"api.example.kg", "b.kg"})  # type: ignore[attr-defined]


def test_without_the_option_the_environment_gives_the_list_and_without_both_any_host_may_be_called(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLOWSTAT_ALLOWED_HOSTS", "a.kg, b.kg")
    assert served_config(tmp_path).allowed_hosts == frozenset({"a.kg", "b.kg"})  # type: ignore[attr-defined]
    monkeypatch.delenv("FLOWSTAT_ALLOWED_HOSTS")
    assert served_config(tmp_path).allowed_hosts is None  # type: ignore[attr-defined]


def _serve_with(tmp_path: Path, *extra: str) -> list[FastAPI]:
    served: list[FastAPI] = []
    main(
        ["--db", str(tmp_path / "f.db"), "--data", str(tmp_path), "--target", "t.db", *extra],
        serve=lambda app, host, port: served.append(app),
    )
    return served


def _logins_file(tmp_path: Path) -> Path:
    path = tmp_path / "tokens.json"
    path.write_text(
        '{"base": "http://127.0.0.1:8000", "tokens": {"designer": "d-token", "chief": "c-token"}}',
        encoding="utf8",
    )
    return path


def test_without_the_option_the_core_has_no_route_that_gives_tokens(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    [app] = _serve_with(tmp_path)
    assert TestClient(app).get("/demo/logins").status_code == 404
    assert TestClient(app).get("/health").json() == {"status": "ok"}  # so the page asks no more


def test_with_the_option_the_page_can_ask_for_the_logins_of_the_demonstration(
    tmp_path: Path,
) -> None:
    from fastapi.testclient import TestClient

    [app] = _serve_with(tmp_path, "--demo-logins", str(_logins_file(tmp_path)))
    reply = TestClient(app).get("/demo/logins")
    assert reply.status_code == 200
    assert reply.headers["cache-control"] == "no-store"
    assert reply.json() == {
        "logins": [
            {"name": "designer", "token": "d-token"},
            {"name": "chief", "token": "c-token"},
        ]
    }
    assert "/demo/logins" not in TestClient(app).get("/openapi.json").text  # not advertised
    # the page learns from /health that the route is there, so it never asks for a missing one
    assert TestClient(app).get("/health").json() == {"status": "ok", "demo_logins": "yes"}


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "example.org"])
def test_the_logins_are_refused_where_others_could_read_them(tmp_path: Path, host: str) -> None:
    with pytest.raises(SystemExit) as stopped:
        _serve_with(tmp_path, "--host", host, "--demo-logins", str(_logins_file(tmp_path)))
    assert stopped.value.code == 2


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_the_logins_are_allowed_on_the_loopback(tmp_path: Path, host: str) -> None:
    assert (
        len(_serve_with(tmp_path, "--host", host, "--demo-logins", str(_logins_file(tmp_path))))
        == 1
    )


def test_a_file_that_has_no_tokens_is_refused(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    for text in ("not json", '{"other": 1}', '{"tokens": [1, 2]}'):
        broken.write_text(text, encoding="utf8")
        with pytest.raises(SystemExit):
            _serve_with(tmp_path, "--demo-logins", str(broken))
    with pytest.raises(SystemExit):
        _serve_with(tmp_path, "--demo-logins", str(tmp_path / "missing.json"))
