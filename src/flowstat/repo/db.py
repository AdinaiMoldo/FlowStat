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
    created_at TEXT    NOT NULL
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
    PRIMARY KEY (run_id, seq)
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
    db.executescript(SCHEMA)
    _add_missing_columns(db)
    return db


# Columns added after the first release of the schema: (table, column, type).
_LATER_COLUMNS = [("run_events", "attempt", "INTEGER"), ("run_events", "delay", "REAL")]


def _add_missing_columns(db: sqlite3.Connection) -> None:
    """A database file of an earlier version gets the columns it lacks; nothing else changes."""
    for table, column, type_ in _LATER_COLUMNS:
        have = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {type_}")
