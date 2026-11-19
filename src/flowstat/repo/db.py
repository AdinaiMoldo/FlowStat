"""SQLite database of the repository: schema and connection.

Published versions are immutable at the database level: triggers refuse UPDATE and DELETE on a
published row, so even a bug in the code above cannot change a published version.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable
from typing import Any, Self

SCHEMA = """
CREATE TABLE IF NOT EXISTS versions (
    name         TEXT    NOT NULL,
    number       INTEGER NOT NULL,
    status       TEXT    NOT NULL CHECK (status IN ('draft', 'published')),
    text         TEXT    NOT NULL,
    catalogue    TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    published_at TEXT,
    PRIMARY KEY (name, number)
);

CREATE TRIGGER IF NOT EXISTS versions_published_no_update
BEFORE UPDATE ON versions WHEN OLD.status = 'published'
BEGIN SELECT RAISE(ABORT, 'published versions are immutable'); END;

CREATE TRIGGER IF NOT EXISTS versions_published_no_delete
BEFORE DELETE ON versions WHEN OLD.status = 'published'
BEGIN SELECT RAISE(ABORT, 'published versions are immutable'); END;

CREATE TABLE IF NOT EXISTS catalogue_items (
    list_name TEXT    NOT NULL,
    position  INTEGER NOT NULL,
    code      TEXT    NOT NULL,
    attrs     TEXT    NOT NULL,
    PRIMARY KEY (list_name, position),
    UNIQUE (list_name, code)
);

CREATE TABLE IF NOT EXISTS runs (
    run_id     TEXT    PRIMARY KEY,
    workflow   TEXT    NOT NULL,
    version    INTEGER NOT NULL,
    inputs     TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    origin     TEXT
);

CREATE TABLE IF NOT EXISTS outbox (
    run_id          TEXT    PRIMARY KEY,
    shipped_through INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ownership (
    run_id  TEXT    PRIMARY KEY,
    epoch   INTEGER NOT NULL,
    state   TEXT    NOT NULL CHECK (state IN ('OWNED', 'MIGRATING', 'MIGRATED_OUT')),
    peer    TEXT,
    version INTEGER
);

CREATE TABLE IF NOT EXISTS handovers (
    run_id  TEXT    PRIMARY KEY,
    plan    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS run_events (
    run_id  TEXT    NOT NULL,
    seq     INTEGER NOT NULL,
    kind    TEXT    NOT NULL,
    at      REAL    NOT NULL,
    step    TEXT,
    state   TEXT,
    outcome TEXT,
    code    TEXT,
    message TEXT,
    output  TEXT,
    attempt INTEGER,
    delay   REAL,
    decision TEXT,
    PRIMARY KEY (run_id, seq)
);

CREATE TABLE IF NOT EXISTS users (
    name       TEXT    PRIMARY KEY,
    role       TEXT    NOT NULL CHECK (role IN ('admin', 'designer', 'operator')),
    titles     TEXT    NOT NULL,
    token_hash TEXT    NOT NULL UNIQUE,
    disabled   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT    PRIMARY KEY,
    user_name  TEXT    NOT NULL REFERENCES users(name),
    created_at TEXT    NOT NULL,
    expires_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS deployments (
    id         TEXT    PRIMARY KEY,
    workflow   TEXT    NOT NULL,
    version    INTEGER NOT NULL,
    kind       TEXT    NOT NULL CHECK (kind IN ('process', 'docker')),
    status     TEXT    NOT NULL CHECK (status IN ('starting', 'running', 'failed', 'stopped')),
    endpoint   TEXT,
    bundle     TEXT    NOT NULL,
    handle     TEXT,
    detail     TEXT,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL,
    ingest_key_sha256 TEXT,
    last_event_at     TEXT,
    control_key       TEXT,
    answering         INTEGER,
    answering_since   TEXT,
    control_key_next  TEXT,
    ingest_key_next_sha256 TEXT
);

CREATE TABLE IF NOT EXISTS campaigns (
    id              TEXT    PRIMARY KEY,
    name            TEXT    NOT NULL UNIQUE,
    year            INTEGER NOT NULL,
    quarter         INTEGER NOT NULL CHECK (quarter BETWEEN 1 AND 4),
    regions         TEXT    NOT NULL,
    deadline        TEXT    NOT NULL,
    workflow        TEXT    NOT NULL,
    workflow_version INTEGER NOT NULL,
    template        TEXT    NOT NULL,
    rules           TEXT    NOT NULL,
    preliminary_min INTEGER NOT NULL,
    final_min       INTEGER NOT NULL,
    approver        TEXT    NOT NULL,
    status          TEXT    NOT NULL,
    created_by      TEXT    NOT NULL,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS form_versions (
    region      TEXT    NOT NULL,
    quarter     TEXT    NOT NULL,
    number      INTEGER NOT NULL,
    sha256      TEXT    NOT NULL,
    size        INTEGER NOT NULL,
    rows        INTEGER NOT NULL,
    uploaded_by TEXT    NOT NULL,
    uploaded_at TEXT    NOT NULL,
    rules       TEXT    NOT NULL,
    findings    TEXT    NOT NULL,
    content     BLOB    NOT NULL,
    removed_at  TEXT,
    removed_by  TEXT,
    PRIMARY KEY (region, quarter, number)
);

CREATE TABLE IF NOT EXISTS problems (
    id          TEXT    PRIMARY KEY,
    region      TEXT    NOT NULL,
    quarter     TEXT    NOT NULL,
    key         TEXT    NOT NULL,
    code        TEXT    NOT NULL,
    indicator   TEXT    NOT NULL,
    cell        TEXT    NOT NULL,
    message     TEXT    NOT NULL,
    actual      TEXT    NOT NULL,
    expected    TEXT    NOT NULL,
    status      TEXT    NOT NULL,
    assignee    TEXT,
    opened_in   INTEGER NOT NULL,
    seen_in     INTEGER NOT NULL,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    UNIQUE (region, quarter, key)
);

CREATE TABLE IF NOT EXISTS problem_events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    problem_id  TEXT    NOT NULL,
    at          TEXT    NOT NULL,
    by          TEXT    NOT NULL,
    kind        TEXT    NOT NULL,
    text        TEXT    NOT NULL,
    version     INTEGER
);

CREATE TRIGGER IF NOT EXISTS problem_events_no_update
BEFORE UPDATE ON problem_events
BEGIN SELECT RAISE(ABORT, 'the history of a problem is append-only'); END;

CREATE TRIGGER IF NOT EXISTS problem_events_no_delete
BEFORE DELETE ON problem_events
BEGIN SELECT RAISE(ABORT, 'the history of a problem is append-only'); END;

CREATE TABLE IF NOT EXISTS service_settings (
    name  TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS run_events_no_update
BEFORE UPDATE ON run_events
BEGIN SELECT RAISE(ABORT, 'the history of a run is append-only'); END;

CREATE TRIGGER IF NOT EXISTS run_events_no_delete
BEFORE DELETE ON run_events
BEGIN SELECT RAISE(ABORT, 'the history of a run is append-only'); END;
"""


class _GuardedCursor(sqlite3.Cursor):
    """A cursor whose every step is taken while the connection is held (``GuardedConnection``)."""

    def _guard(self) -> threading.RLock:
        connection = self.connection
        assert isinstance(connection, GuardedConnection)
        return connection.guard

    def execute(self, sql: str, parameters: Any = (), /) -> Self:
        with self._guard():
            return super().execute(sql, parameters)

    def executemany(self, sql: str, seq_of_parameters: Iterable[Any], /) -> Self:
        with self._guard():
            return super().executemany(sql, seq_of_parameters)

    def executescript(self, sql_script: str, /) -> sqlite3.Cursor:
        with self._guard():
            return super().executescript(sql_script)

    def fetchone(self) -> Any:
        with self._guard():
            return super().fetchone()

    def fetchmany(self, size: int | None = 1) -> list[Any]:
        with self._guard():
            return super().fetchmany(size)

    def fetchall(self) -> list[Any]:
        with self._guard():
            return super().fetchall()

    def __next__(self) -> Any:
        with self._guard():
            return super().__next__()


class GuardedConnection(sqlite3.Connection):
    """A connection that cannot be closed while another thread is inside a call on it (DEF-1).

    A web framework makes the connection of a request in one worker; when the request is cancelled
    (a browser that goes to another page) the connection is closed while the endpoint may still be
    running on it in a worker thread. ``sqlite3`` releases the interpreter lock during a step, so
    ``close`` could free the connection under it: a segmentation fault, which killed the whole
    test run of the Ubuntu job at random. Here every call that touches the database, and
    ``close``, take one lock. A call that is under way finishes, and ``close`` follows it; a call
    that comes after ``close`` gets the usual ``ProgrammingError`` (the connection is closed)
    instead of a crash.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.guard = threading.RLock()

    def cursor(self, factory: Any = _GuardedCursor) -> Any:
        return super().cursor(factory)

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        cursor: sqlite3.Cursor = self.cursor().execute(sql, parameters)
        return cursor

    def executemany(self, sql: str, parameters: Iterable[Any], /) -> sqlite3.Cursor:
        cursor: sqlite3.Cursor = self.cursor().executemany(sql, parameters)
        return cursor

    def executescript(self, sql_script: str, /) -> sqlite3.Cursor:
        cursor: sqlite3.Cursor = self.cursor().executescript(sql_script)
        return cursor

    def commit(self) -> None:
        with self.guard:
            super().commit()

    def rollback(self) -> None:
        with self.guard:
            super().rollback()

    def close(self) -> None:
        with self.guard:
            super().close()


def open_database(path: str) -> sqlite3.Connection:
    """Open (and create if needed) the database. ``":memory:"`` gives a private test database."""
    # autocommit; transactions are explicit
    db = sqlite3.connect(path, isolation_level=None, factory=GuardedConnection)
    # An event that was committed must survive a power loss: the log is written before the
    # action runs (docs/dsl-spec.md 5.5), so a lost event would be a lost fact.
    db.execute("PRAGMA synchronous = FULL")
    db.executescript(SCHEMA)
    _add_missing_columns(db)
    return db


def connect(path: str) -> sqlite3.Connection:
    """A connection to a database that already has its schema, for one request or one driver.

    It may be used by another thread than the one that made it (a web framework makes the
    connection in one worker and uses it in another), always by one thread at a time. Waiting for
    a lock is limited to 10 seconds. It is a ``GuardedConnection``: closing it while another
    thread is still inside a call on it waits for that call instead of crashing the process.
    """
    db = sqlite3.connect(
        path,
        isolation_level=None,
        check_same_thread=False,
        timeout=10.0,
        factory=GuardedConnection,
    )
    db.execute("PRAGMA synchronous = FULL")
    return db


# Columns added after the first release of the schema: (table, column, type).
_LATER_COLUMNS = [
    ("run_events", "attempt", "INTEGER"),
    ("run_events", "delay", "REAL"),
    ("run_events", "decision", "TEXT"),
    ("runs", "origin", "TEXT"),
    ("deployments", "ingest_key_sha256", "TEXT"),
    ("deployments", "last_event_at", "TEXT"),
    ("deployments", "control_key", "TEXT"),
    ("deployments", "answering", "INTEGER"),
    ("deployments", "answering_since", "TEXT"),
    ("deployments", "control_key_next", "TEXT"),
    ("deployments", "ingest_key_next_sha256", "TEXT"),
    ("users", "password_hash", "TEXT"),
    ("users", "regions", "TEXT"),  # US-64: a JSON list; NULL or [] is the central office
]


def _add_missing_columns(db: sqlite3.Connection) -> None:
    """A database file of an earlier version gets the columns it lacks; nothing else changes."""
    for table, column, type_ in _LATER_COLUMNS:
        have = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {type_}")
