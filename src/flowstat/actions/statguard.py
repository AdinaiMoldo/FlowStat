"""statguard.validate: the content rules of a form, as a step of a workflow (US-54).

The rules are ``flowstat.model.rules``; this module only turns what they find into the failure of
a step. Without the argument ``rules`` the basic rules apply, as they always did.
"""

from __future__ import annotations

from collections.abc import Sequence

from flowstat.actions.base import ActionContext, ActionError, Args
from flowstat.model import rules as content

SHOWN = 3  # violations quoted in the message; the count tells how many there are


def violations(rows: Sequence[object]) -> list[str]:
    """The basic rules the rows break, one sentence for each; empty when the form is fine."""
    return [f.message for f in content.blocking(content.check(rows))]


def validate(args: Args, ctx: ActionContext) -> dict[str, object]:
    rows = args["rows"]
    assert isinstance(rows, list)
    name = args.get("rules", content.BASIC.name)
    if not isinstance(name, str) or not content.known(name):
        raise ActionError("E-ACT-009", f"there is no rule set {name!r}")
    found = [f.message for f in content.blocking(content.check(rows, content.named(name)))]
    if found:
        more = f" (and {len(found) - SHOWN} more)" if len(found) > SHOWN else ""
        raise ActionError(
            "E-ACT-005", f"{len(found)} rule violations: " + "; ".join(found[:SHOWN]) + more
        )
    return {"rows": rows, "checked": len(rows)}
