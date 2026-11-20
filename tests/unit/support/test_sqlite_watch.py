"""DEF-2: the report of the open SQLite connections names the read that blocks a writer."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from flowstat.repo.db import connect, open_database
from tests.support import sqlite_watch


@pytest.fixture
def watched() -> Iterator[None]:
    sqlite_watch.install()
    yield
    sqlite_watch.uninstall()


def filled(tmp_path: Path) -> str:
    path = str(tmp_path / "t.db")
    db = open_database(path)
    for n in range(5):
        db.execute(
            "INSERT INTO runs (run_id, workflow, version, inputs, created_at) VALUES (?,?,?,?,?)",
            (f"r{n}", "w", 1, "{}", "t"),
        )
    db.close()
    return path


def left_in_the_middle(path: str) -> tuple[sqlite3.Connection, sqlite3.Cursor]:
    reader = connect(path)
    cursor = reader.execute("SELECT run_id FROM runs ORDER BY run_id")
    cursor.fetchone()  # four rows are still to come: the read holds its lock
    return reader, cursor


def test_a_read_left_in_the_middle_blocks_a_writer_and_the_report_names_it(
    watched: None, tmp_path: Path
) -> None:
    path = filled(tmp_path)
    reader, cursor = left_in_the_middle(path)
    writer = sqlite3.connect(path, isolation_level=None, timeout=0.2)
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        writer.execute("DELETE FROM runs WHERE run_id = 'r0'")
    report = sqlite_watch.report()
    assert "SELECT run_id FROM runs ORDER BY run_id" in report
    assert "left_in_the_middle" in report  # who opened the connection
    assert "t.db" in report
    cursor.close()
    writer.execute("DELETE FROM runs WHERE run_id = 'r0'")  # the read is over: the writer goes on
    reader.close()
    writer.close()


def test_a_connection_that_was_closed_is_not_in_the_report(watched: None, tmp_path: Path) -> None:
    path = filled(tmp_path)
    reader, cursor = left_in_the_middle(path)
    cursor.close()
    reader.close()
    del reader, cursor
    assert "SELECT run_id FROM runs ORDER BY run_id" not in sqlite_watch.report()


def test_a_read_that_was_finished_is_told_apart_from_one_that_was_not(
    watched: None, tmp_path: Path
) -> None:
    path = filled(tmp_path)
    reader = connect(path)
    done = reader.execute("SELECT workflow FROM runs")
    done.fetchall()
    half = reader.execute("SELECT version FROM runs")
    half.fetchone()
    report = sqlite_watch.report()
    line_done = next(ln for ln in report.splitlines() if "SELECT workflow FROM runs" in ln)
    line_half = next(ln for ln in report.splitlines() if "SELECT version FROM runs" in ln)
    assert "finished" in line_done and "NOT FINISHED" in line_half
    reader.close()


def test_the_watch_is_shared_and_goes_when_the_last_caller_lets_go(watched: None) -> None:
    patched = sqlite3.connect
    sqlite_watch.install()  # a second caller
    sqlite_watch.uninstall()
    assert sqlite3.connect is patched  # the first caller still has it


def test_a_place_is_the_last_folder_and_the_file_on_either_system() -> None:
    assert sqlite_watch._place("/home/x/flowstat/repo/db.py") == "repo/db.py"
    assert sqlite_watch._place("D:\\a\\FlowStat\\src\\flowstat\\repo\\db.py") == "repo/db.py"
    assert sqlite_watch._place("db.py") == "db.py"


def test_the_report_has_the_statement_that_waited_for_the_lock_and_the_read_that_held_it(
    watched: None, tmp_path: Path
) -> None:
    path = filled(tmp_path)
    reader, cursor = left_in_the_middle(path)
    writer = connect(path)
    writer.execute("PRAGMA busy_timeout = 300")
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        writer.execute("DELETE FROM runs WHERE run_id = 'r1'")
    report = sqlite_watch.report()
    waited = next(ln for ln in report.splitlines() if "DELETE FROM runs" in ln)
    assert "ERROR database is locked" in waited and "execute 0." in waited
    held = [ln for ln in report.splitlines() if "read NOT FINISHED" in ln]
    assert any("SELECT run_id FROM runs ORDER BY run_id" in ln for ln in held)
    assert "open in this process (pid " in report
    cursor.close()
    reader.close()
    writer.close()


def test_a_connection_that_another_thread_opened_is_listed(watched: None, tmp_path: Path) -> None:
    import threading

    path = filled(tmp_path)
    made: list[sqlite3.Connection] = []
    worker = threading.Thread(target=lambda: made.append(sqlite3.connect(path)), name="elsewhere")
    worker.start()
    worker.join()
    report = sqlite_watch.report()
    assert "opened in thread elsewhere" in report  # its state can be read from this thread
