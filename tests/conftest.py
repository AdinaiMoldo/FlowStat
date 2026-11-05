"""Shared fixtures."""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def close_databases(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Close every database a test opened, so no connection is left to the garbage collector."""
    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        connection: sqlite3.Connection = real_connect(*args, **kwargs)  # type: ignore[call-overload]
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    yield
    for connection in opened:
        # opened in a worker thread of a run: the action that opened it closed it
        with contextlib.suppress(sqlite3.ProgrammingError):
            connection.close()
