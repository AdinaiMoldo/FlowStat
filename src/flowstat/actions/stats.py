"""stats.aggregate: the national total of the regional rows."""

from __future__ import annotations

import math

from flowstat.actions.base import ActionContext, Args


def aggregate(args: Args, ctx: ActionContext) -> dict[str, object]:
    """Sum ``value`` by ``code`` over all regions. Rows come out in code order (deterministic)."""
    parts = args["parts"]
    assert isinstance(parts, list)
    names: dict[str, object] = {}
    values: dict[str, list[float]] = {}
    for part in parts:
        for row in part:
            code = row["code"]
            names.setdefault(code, row["name"])
            values.setdefault(code, []).append(row["value"])
    rows = [{"code": c, "name": names[c], "value": math.fsum(values[c])} for c in sorted(values)]
    return {"rows": rows, "regions": len(parts)}
