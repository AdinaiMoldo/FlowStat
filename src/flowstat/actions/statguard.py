"""statguard.validate: statistical rules for the rows of a form."""

from __future__ import annotations

from flowstat.actions.base import ActionContext, ActionError, Args

SHOWN = 3  # violations quoted in the message; the count tells how many there are


def violations(rows: list[object]) -> list[str]:
    """The rules the rows of a form break, one sentence for each; empty when the form is fine."""
    if not rows:
        return ["the form has no rows"]
    found: list[str] = []
    seen: set[object] = set()
    for number, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            found.append(f"row {number}: not a record")
            continue
        code = row.get("code")
        value = row.get("value")
        if not isinstance(code, str) or not code.strip():
            found.append(f"row {number}: code is empty")
        elif code in seen:
            found.append(f"row {number}: duplicate code {code}")
        else:
            seen.add(code)
        if isinstance(value, bool) or not isinstance(value, int | float):
            found.append(f"row {number} (code {code}): value is not a number")
        elif value < 0:
            found.append(f"row {number} (code {code}): value is negative")
    return found


def validate(args: Args, ctx: ActionContext) -> dict[str, object]:
    rows = args["rows"]
    assert isinstance(rows, list)
    found = violations(rows)
    if found:
        more = f" (and {len(found) - SHOWN} more)" if len(found) > SHOWN else ""
        raise ActionError(
            "E-ACT-005", f"{len(found)} rule violations: " + "; ".join(found[:SHOWN]) + more
        )
    return {"rows": rows, "checked": len(rows)}
