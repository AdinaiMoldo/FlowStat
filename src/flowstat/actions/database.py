"""db.write and db.delete_batch: the results go to the target database in batches.

A batch is the run that wrote it, so writing twice replaces instead of duplicating (the action is
idempotent) and a batch can be removed as a whole (the compensation of ``publish``).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from flowstat.actions.base import ActionContext, ActionError, Args

TABLES = """
CREATE TABLE IF NOT EXISTS published (
    batch TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL, value REAL NOT NULL,
    PRIMARY KEY (batch, code)
);
CREATE TABLE IF NOT EXISTS outbox (
    run_id TEXT NOT NULL, step TEXT NOT NULL, message TEXT NOT NULL,
    PRIMARY KEY (run_id, step)
);
"""


@contextmanager
def target_database(ctx: ActionContext) -> Iterator[sqlite3.Connection]:
    """One short connection per call: several steps may write from several threads."""
    if ctx.target is None:
        raise ActionError("E-ACT-006", "no target database is configured")
    try:
        db = sqlite3.connect(ctx.target, timeout=30, isolation_level=None)
        db.executescript(TABLES)
    except sqlite3.Error as exc:
        raise ActionError("E-ACT-006", f"cannot open the target database: {exc}") from None
    try:
        yield db
    finally:
        db.close()


def write_rows(args: Args, ctx: ActionContext) -> dict[str, object]:
    rows = args["rows"]
    assert isinstance(rows, list)
    with target_database(ctx) as db:
        try:
            db.execute("BEGIN IMMEDIATE")
            for row in rows:
                db.execute(
                    "INSERT OR REPLACE INTO published (batch, code, name, value) VALUES (?,?,?,?)",
                    (ctx.run_id, row["code"], row["name"], row["value"]),
                )
            db.execute("COMMIT")
        except (KeyError, TypeError, sqlite3.Error) as exc:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise ActionError("E-ACT-006", f"cannot write the rows: {exc!r}") from None
    return {"written": len(rows), "batch": ctx.run_id}


def delete_batch(args: Args, ctx: ActionContext) -> dict[str, object]:
    with target_database(ctx) as db:
        try:
            deleted = db.execute("DELETE FROM published WHERE batch = ?", (ctx.run_id,)).rowcount
        except sqlite3.Error as exc:
            raise ActionError("E-ACT-006", f"cannot delete the batch: {exc}") from None
    return {"deleted": deleted}
