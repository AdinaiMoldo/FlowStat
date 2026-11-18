"""DEF-1: closing a connection while another thread is still running a query on it must not crash
the process.

A web framework makes the connection of a request in one worker and the endpoint may still be
running on it in another thread when the request is cancelled (the browser went to another page)
and the connection is closed. With a plain ``sqlite3`` connection that is a use after free: the
interpreter dies with a segmentation fault (the exit 139 of the Ubuntu job). The script below
is run in a process of its own, because that is the only way to see a crash.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[3] / "src"

CLOSE_UNDER_LOAD = """
import sqlite3, threading
from flowstat.repo.db import connect

db = connect(":memory:")
db.execute("CREATE TABLE t (x INTEGER)")
db.executemany("INSERT INTO t VALUES (?)", [(n,) for n in range(2000)])
stop = threading.Event()
errors = set()

def hammer():
    while not stop.is_set():
        try:
            cursor = db.execute("SELECT sum(x), count(*) FROM t")
            cursor.fetchone()
            for _ in db.execute("SELECT x FROM t LIMIT 50"):
                pass
        except sqlite3.ProgrammingError as error:
            errors.add(str(error))
            return

workers = [threading.Thread(target=hammer) for _ in range(8)]
for w in workers:
    w.start()
for _ in range(200):
    db.execute("SELECT 1").fetchall()
db.close()
stop.set()
for w in workers:
    w.join()
print("survived", sorted(errors))
"""


def run(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        env={"PYTHONPATH": str(SRC), "PATH": ""},
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_threads_that_keep_asking_while_the_connection_is_closed_get_an_error_not_a_crash() -> None:
    # without the guard this dies in seven of eight runs; three runs make a miss very unlikely
    for _ in range(3):
        done = run(CLOSE_UNDER_LOAD)
        assert done.returncode == 0, f"exit {done.returncode}: {done.stderr[-400:]}"
        assert "survived" in done.stdout


def test_a_closed_connection_refuses_every_kind_of_use() -> None:
    from flowstat.repo.db import connect

    db = connect(":memory:")
    cursor = db.execute("SELECT 1")
    db.close()
    for use in (
        lambda: db.execute("SELECT 1"),
        lambda: db.executemany("SELECT ?", [(1,)]),
        lambda: db.executescript("SELECT 1"),
        lambda: db.commit(),
        lambda: cursor.fetchall(),
    ):
        with pytest.raises(Exception, match="closed"):
            use()
    db.close()  # closing twice is as harmless as it always was


def test_a_connection_works_as_it_did_in_every_other_respect() -> None:
    from flowstat.repo.db import connect

    db = connect(":memory:")
    db.execute("CREATE TABLE t (a INTEGER, b TEXT)")
    db.executemany("INSERT INTO t VALUES (?, ?)", [(1, "x"), (2, "y"), (3, "z")])
    assert db.execute("SELECT a FROM t WHERE b = ?", ("y",)).fetchone() == (2,)
    assert [r[0] for r in db.execute("SELECT a FROM t ORDER BY a")] == [1, 2, 3]
    assert db.execute("SELECT a FROM t ORDER BY a").fetchmany(2) == [(1,), (2,)]
    cursor = db.cursor()
    cursor.execute("UPDATE t SET b = 'q' WHERE a = 1")
    assert cursor.rowcount == 1 and cursor.lastrowid is not None
    db.execute("BEGIN")
    db.execute("DELETE FROM t")
    db.rollback()
    assert db.execute("SELECT count(*) FROM t").fetchone() == (3,)
    with pytest.raises(Exception, match=r"UNIQUE|constraint"):
        db.execute("CREATE TABLE u (a INTEGER PRIMARY KEY)")
        db.execute("INSERT INTO u VALUES (1)")
        db.execute("INSERT INTO u VALUES (1)")
    db.close()
