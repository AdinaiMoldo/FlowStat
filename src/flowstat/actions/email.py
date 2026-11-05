"""email.send: puts a message in the outbox table. Nothing is sent; real mail is out of scope."""

from __future__ import annotations

from flowstat.actions.base import ActionContext, Args
from flowstat.actions.database import target_database


def send_email(args: Args, ctx: ActionContext) -> dict[str, object]:
    with target_database(ctx) as db:
        db.execute(
            "INSERT OR REPLACE INTO outbox (run_id, step, message) VALUES (?,?,?)",
            (ctx.run_id, ctx.step, "returned to the regions"),
        )
    return {"queued": 1}
