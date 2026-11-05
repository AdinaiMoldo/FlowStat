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
"""


def open_database(path: str) -> sqlite3.Connection:
    """Open (and create if needed) the database. ``":memory:"`` gives a private test database."""
    db = sqlite3.connect(path, isolation_level=None)  # autocommit; transactions are explicit
    db.executescript(SCHEMA)
    return db
