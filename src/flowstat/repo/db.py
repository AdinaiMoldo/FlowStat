"""SQLite database of the repository: schema and connection.

Published versions are immutable at the database level: triggers refuse UPDATE and DELETE on a
published row, so even a bug in the code above cannot change a published version.
"""

from __future__ import annotations

import sqlite3

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


def open_database(path: str) -> sqlite3.Connection:
    """Open (and create if needed) the database. ``":memory:"`` gives a private test database."""
    db = sqlite3.connect(path, isolation_level=None)  # autocommit; transactions are explicit
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
    a lock is limited to 10 seconds.
    """
    db = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=10.0)
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
]


def _add_missing_columns(db: sqlite3.Connection) -> None:
    """A database file of an earlier version gets the columns it lacks; nothing else changes."""
    for table, column, type_ in _LATER_COLUMNS:
        have = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {type_}")
