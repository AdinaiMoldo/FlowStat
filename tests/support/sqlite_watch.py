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

import collections
import gc
import os
import sqlite3
import threading
import time
import traceback
from typing import Any

from flowstat.repo import db as repo_db

_lock = threading.Lock()
_original_connect: Any = None
_original_execute: Any = None
_original_fetchone: Any = None
_original_fetchall: Any = None
_original_next: Any = None
_original_cursor_close: Any = None
_original_close: Any = None
# id of a connection -> (the database, the thread and the stack that opened it). A connection of
# sqlite3 cannot be weakly referenced, so the key is its id; every connection made while the watch
# is on overwrites the entry of a dead one that had the same id.
_opened: dict[int, tuple[str, str, list[str]]] = {}
_KEPT = 5000
# The statements of the cursors of flowstat.repo.db, the newest last: [started, thread,
# connection, statement, seconds in execute, seconds until the read was finished or None, error]
_recent: collections.deque[list[Any]] = collections.deque(maxlen=600)
_WINDOW = 30.0  # seconds before the report that are shown
_users = 0  # how many callers asked for the watch; it goes when the last one lets go
_HERE = __file__


def _place(filename: str) -> str:
    """The last folder and the file, with either kind of path separator."""
    parts = filename.replace("\\", "/").split("/")
    return "/".join(parts[-2:])


def _stack() -> list[str]:
    frames = [f for f in traceback.extract_stack()[:-2] if f.filename != _HERE]
    return [f"{_place(f.filename)}:{f.lineno} {f.name}" for f in frames[-6:]]


def install() -> None:
    """Start remembering. A second caller shares the watch of the first."""
    global _original_connect, _original_execute, _original_fetchone, _original_fetchall
    global _original_next, _original_cursor_close, _original_close, _users
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
        _original_cursor_close = cursor.close
        _original_close = repo_db.GuardedConnection.close

        def connect(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
            made: sqlite3.Connection = _original_connect(database, *args, **kwargs)
            with _lock:
                if len(_opened) > _KEPT:  # forget the dead ones; a leaked one must stay known
                    alive = {id(t) for t in gc.get_objects() if isinstance(t, sqlite3.Connection)}
                    for key in [k for k in _opened if k not in alive]:
                        del _opened[key]
                _opened[id(made)] = (str(database), threading.current_thread().name, _stack())
            return made

        def finished(cursor: Any) -> None:
            cursor.watch_done = True
            entry = getattr(cursor, "watch_entry", None)
            if entry is not None and entry[5] is None:
                entry[5] = time.monotonic() - entry[0]

        def execute(self: Any, sql: str, parameters: Any = (), /) -> Any:
            self.watch_sql = sql
            self.watch_done = False
            started = time.monotonic()
            text = " ".join(str(sql).split())[:110]
            entry = [started, threading.current_thread().name, id(self.connection), text, None,
                     None, None]  # fmt: skip
            self.watch_entry = entry
            _recent.append(entry)
            try:
                return _original_execute(self, sql, parameters)
            except sqlite3.Error as error:
                entry[6] = str(error)
                raise
            finally:
                entry[4] = time.monotonic() - started

        def fetchone(self: Any) -> Any:
            row = _original_fetchone(self)
            if row is None:
                finished(self)
            return row

        def fetchall(self: Any) -> Any:
            rows = _original_fetchall(self)
            finished(self)
            return rows

        def step(self: Any) -> Any:
            try:
                return _original_next(self)
            except StopIteration:
                finished(self)
                raise

        def cursor_close(self: Any) -> None:
            finished(self)  # a closed cursor holds nothing
            _original_cursor_close(self)

        def close(self: Any) -> None:
            mine = id(self)
            for entry in list(_recent):
                if entry[2] == mine and entry[5] is None:
                    entry[5] = time.monotonic() - entry[0]  # closed: its reads are over
            _original_close(self)

        sqlite3.connect = connect  # type: ignore[assignment]
        cursor.close = cursor_close  # type: ignore[method-assign]
        repo_db.GuardedConnection.close = close  # type: ignore[method-assign]
        cursor.execute = execute  # type: ignore[method-assign]
        cursor.fetchone = fetchone  # type: ignore[method-assign]
        cursor.fetchall = fetchall  # type: ignore[method-assign]
        cursor.__next__ = step  # type: ignore[method-assign]


def uninstall() -> None:
    global _original_connect, _original_execute, _original_fetchone, _original_fetchall
    global _original_next, _original_cursor_close, _original_close, _users
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
        cursor.close = _original_cursor_close  # type: ignore[method-assign]
        repo_db.GuardedConnection.close = _original_close  # type: ignore[method-assign]
        _original_cursor_close = _original_close = None
        _original_connect = _original_execute = None
        _opened.clear()
        _recent.clear()
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
    databases: set[str] = set()
    number = 0
    for connection in connections:
        database, thread, stack = _opened.get(
            id(connection), ("?", "?", ["(opened before the watch)"])
        )
        try:
            connection.total_changes  # noqa: B018  (raises when the connection is closed)
        except sqlite3.ProgrammingError as error:
            if "closed" in str(error):
                continue  # closed and waiting for the collector: it holds nothing
            busy = "of another thread, so its state cannot be read from here"
        else:
            busy = "in a transaction" if connection.in_transaction else "no transaction"
        number += 1
        databases.add(database)
        lines.append(f"[{number}] {database} ({busy}; opened in thread {thread})")
        lines.append("    opened by: " + " <- ".join(reversed(stack)))
        for cursor in cursors.get(id(connection), []):
            lines.append(_state(cursor))
    head = f"{number} SQLite connection(s) are open in this process (pid {os.getpid()})"
    return "\n".join([head, *lines, *_others(databases), *_history()])


def _others(databases: set[str]) -> list[str]:
    """Other processes that have one of the databases open (Linux: read from /proc)."""
    lines: list[str] = []
    wanted = {os.path.realpath(d) for d in databases if d not in ("?", ":memory:")}
    wanted |= {f"{d}-journal" for d in wanted}
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit() and int(p) != os.getpid()]
    except OSError:
        return ["other processes: not known on this system"]
    for pid in pids:
        try:
            links = {os.readlink(f"/proc/{pid}/fd/{fd}") for fd in os.listdir(f"/proc/{pid}/fd")}
        except OSError:
            continue  # the process is gone, or it is not ours to look at
        held = sorted(links & wanted)
        if held:
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as stream:
                    command = stream.read().replace(b"\0", b" ").decode(errors="replace")[:200]
            except OSError:
                command = "?"
            lines.append(f"other process {pid} has {', '.join(held)} open: {command}")
    return lines or ["no other process has these databases open"]


def _history() -> list[str]:
    """The statements of the last seconds that were slow, failed, or were reads left unfinished."""
    now = time.monotonic()
    recent = [e for e in list(_recent) if now - e[0] <= _WINDOW]
    lines = [f"{len(recent)} statement(s) in the last {_WINDOW:.0f} s; those worth a look:"]
    for started, thread, connection, text, ran, done, error in recent:
        read = text.upper().startswith(("SELECT", "WITH"))
        slow = ran is not None and ran > 0.2
        open_read = read and done is None
        long_read = read and done is not None and done > 0.2
        if not (slow or open_read or long_read or error):
            continue
        took = "still in execute" if ran is None else f"execute {ran:.2f} s"
        end = (
            ""
            if not read
            else (", read NOT FINISHED" if done is None else f", read done after {done:.2f} s")
        )
        fault = f", ERROR {error}" if error else ""
        when = f"    -{now - started:5.1f} s  {took}{end}{fault}"
        lines.append(f"{when}  [{thread}, connection {connection}]  {text}")
    return lines
