"""Who holds the database? A report of the SQLite connections that are open in this process.

Written for DEF-2: now and then a browser test on the Ubuntu runner fails with ``database is
locked``. In the journal mode the project uses, a read of several rows that was left in the middle
holds a lock that stops every writer, and the connection that holds it is somewhere in this
process (the server of the browser tests runs in a thread of the test process). When the error
comes, this report says which connections are open, who opened each, and which statements were
started on them and not read to the end.

For tests only. ``install`` replaces ``sqlite3.connect`` and the ``execute`` of the cursors of
``flowstat.repo.db`` with versions that remember; ``uninstall`` puts the originals back.
"""

from __future__ import annotations

import gc
import sqlite3
import threading
import traceback
from typing import Any

from flowstat.repo import db as repo_db

_lock = threading.Lock()
_original_connect: Any = None
_original_execute: Any = None
_original_fetchone: Any = None
_original_fetchall: Any = None
_original_next: Any = None
# id of a connection -> (the database, the thread and the stack that opened it). A connection of
# sqlite3 cannot be weakly referenced, so the key is its id; every connection made while the watch
# is on overwrites the entry of a dead one that had the same id.
_opened: dict[int, tuple[str, str, list[str]]] = {}
_KEPT = 5000
_users = 0  # how many callers asked for the watch; it goes when the last one lets go
_HERE = __file__


def _stack() -> list[str]:
    frames = [f for f in traceback.extract_stack()[:-2] if f.filename != _HERE]
    return [f"{f.filename.rsplit('/', 2)[-2]}/{f.filename.rsplit('/', 1)[-1]}:{f.lineno} {f.name}"
            for f in frames[-6:]]  # fmt: skip


def install() -> None:
    """Start remembering. A second caller shares the watch of the first."""
    global _original_connect, _original_execute, _original_fetchone, _original_fetchall
    global _original_next, _users
    with _lock:
        _users += 1
        if _original_connect is not None:
            return
        _original_connect = sqlite3.connect
        cursor = repo_db._GuardedCursor
        _original_execute = cursor.execute
        _original_fetchone = cursor.fetchone
        _original_fetchall = cursor.fetchall
        _original_next = cursor.__next__

        def connect(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
            made: sqlite3.Connection = _original_connect(database, *args, **kwargs)
            with _lock:
                if len(_opened) > _KEPT:  # forget the dead ones; a leaked one must stay known
                    alive = {id(t) for t in gc.get_objects() if isinstance(t, sqlite3.Connection)}
                    for key in [k for k in _opened if k not in alive]:
                        del _opened[key]
                _opened[id(made)] = (str(database), threading.current_thread().name, _stack())
            return made

        def execute(self: Any, sql: str, parameters: Any = (), /) -> Any:
            self.watch_sql = sql
            self.watch_done = False
            return _original_execute(self, sql, parameters)

        def fetchone(self: Any) -> Any:
            row = _original_fetchone(self)
            if row is None:
                self.watch_done = True
            return row

        def fetchall(self: Any) -> Any:
            rows = _original_fetchall(self)
            self.watch_done = True
            return rows

        def step(self: Any) -> Any:
            try:
                return _original_next(self)
            except StopIteration:
                self.watch_done = True
                raise

        sqlite3.connect = connect  # type: ignore[assignment]
        cursor.execute = execute  # type: ignore[method-assign]
        cursor.fetchone = fetchone  # type: ignore[method-assign]
        cursor.fetchall = fetchall  # type: ignore[method-assign]
        cursor.__next__ = step  # type: ignore[method-assign]


def uninstall() -> None:
    global _original_connect, _original_execute, _original_fetchone, _original_fetchall
    global _original_next, _users
    with _lock:
        _users = max(0, _users - 1)
        if _original_connect is None or _users > 0:
            return
        sqlite3.connect = _original_connect
        cursor = repo_db._GuardedCursor
        cursor.execute = _original_execute  # type: ignore[method-assign]
        cursor.fetchone = _original_fetchone  # type: ignore[method-assign]
        cursor.fetchall = _original_fetchall  # type: ignore[method-assign]
        cursor.__next__ = _original_next  # type: ignore[method-assign]
        _original_connect = _original_execute = None
        _opened.clear()
        _original_fetchone = _original_fetchall = _original_next = None


def _state(cursor: sqlite3.Cursor) -> str:
    sql = getattr(cursor, "watch_sql", None)
    if sql is None:
        return "    cursor: statement not known (not a cursor of flowstat.repo.db)"
    text = " ".join(str(sql).split())[:160]
    if not text.upper().startswith(("SELECT", "PRAGMA", "WITH")):
        return f"    cursor: {text}"
    done = "finished" if getattr(cursor, "watch_done", False) else "NOT FINISHED (may hold a lock)"
    return f"    cursor: {done}: {text}"


def report() -> str:
    """The open connections of this process, each with who opened it and its live cursors."""
    cursors: dict[int, list[sqlite3.Cursor]] = {}
    connections: list[sqlite3.Connection] = []
    for thing in gc.get_objects():
        try:
            if isinstance(thing, sqlite3.Cursor):
                cursors.setdefault(id(thing.connection), []).append(thing)
            elif isinstance(thing, sqlite3.Connection):
                connections.append(thing)
        except ReferenceError:
            continue
    lines: list[str] = []
    number = 0
    for connection in connections:
        database, thread, stack = _opened.get(
            id(connection), ("?", "?", ["(opened before the watch)"])
        )
        try:
            connection.total_changes  # noqa: B018  (raises when the connection is closed)
        except sqlite3.ProgrammingError:
            continue  # closed and waiting for the collector: it holds nothing
        busy = "in a transaction" if connection.in_transaction else "no transaction"
        number += 1
        lines.append(f"[{number}] {database} ({busy}; opened in thread {thread})")
        lines.append("    opened by: " + " <- ".join(reversed(stack)))
        for cursor in cursors.get(id(connection), []):
            lines.append(_state(cursor))
    return "\n".join([f"{number} SQLite connection(s) are open in this process", *lines])
