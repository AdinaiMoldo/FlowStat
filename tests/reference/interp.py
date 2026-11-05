"""A reference interpreter of workflows (the oracle of C2 and C3, M5).

It is written from ``docs/dsl-spec.md`` (sections 3.1, 5 and 5.1) and from the table of join
examples in ``test_interp.py``, and shares no code with the engine, the scheduler or the analysis.
It takes the compiled plan only as data (the steps, their ``needs``, ``join``, ``when``), runs it
one step at a time in topological order, and enumerates the executions that a test wants to compare
with: every combination of the values on a grid and of the steps that fail.

Scope. The failure policy ``fail`` ends the run, and what is running at that moment depends on
timing (spec 5.4), so the enumeration lets only steps with ``on_failure: continue`` fail; an
execution in which another step fails is a run that stops, and nothing starts after it that had not
started already. Retries, waiting, ``ensures`` and compensation are not modelled.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from flowstat.compiler.plan import Plan
from flowstat.dsl.ast import (
    Binary,
    Call,
    Compare,
    Expr,
    ListLit,
    Literal,
    Name,
    Ref,
    Unary,
)

SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
SKIPPED = "SKIPPED"
UPSTREAM_FAILED = "UPSTREAM_FAILED"
BAD = (FAILED, UPSTREAM_FAILED)


class Fail(Exception):
    """An expression cannot be evaluated; the step that evaluates it FAILS (spec 3.1)."""


# -- the join rules (spec 5 and 5.1) -----------------------------------------------------------
def join_result(kind: str, k: int, deps: Sequence[str]) -> str | None:
    """``None`` when the join is satisfied, else the state the step takes without running."""
    ok = sum(d == SUCCEEDED for d in deps)
    bad = sum(d in BAD for d in deps)
    if kind == "all_done":
        return None
    if kind == "all_success":
        satisfied = ok == len(deps)
        short = bad > 0
    elif kind == "any_success":
        satisfied = ok >= 1
        short = bad > 0
    elif kind == "at_least":
        satisfied = ok >= k
        short = bad > 0 and ok + bad >= k
    else:  # pragma: no cover - the shape check forbids it
        raise ValueError(kind)
    if satisfied:
        return None
    return UPSTREAM_FAILED if short else SKIPPED


# -- the meaning of an expression (spec 3.1) ---------------------------------------------------
def _number(v: object) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def equal(a: object, b: object) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return a == b


def value(expr: Expr, env: Mapping[str, object], done: frozenset[str]) -> object:
    """Evaluate ``expr``. ``env`` maps ``inputs.x`` and ``step.field`` to values; a step's fields
    can be read only when the step is in ``done`` (it SUCCEEDED)."""
    match expr:
        case Literal(value=v):
            return v
        case ListLit(items=items):
            return [value(i, env, done) for i in items]
        case Ref(path=path):
            head, field = (str(p.value) for p in path[:2])
            if len(path) != 2 or (head != "inputs" and head not in done):
                raise Fail("no value")
            return env[f"{head}.{field}"]
        case Unary(op="not", operand=operand):
            v = value(operand, env, done)
            if not isinstance(v, bool):
                raise Fail("not needs a boolean")
            return not v
        case Unary(op="-", operand=operand):
            v = value(operand, env, done)
            if not _number(v):
                raise Fail("minus needs a number")
            return -v  # type: ignore[operator]
        case Binary(op="and" | "or" as op, left=left, right=right):
            a = value(left, env, done)
            if not isinstance(a, bool):
                raise Fail("and, or need booleans")
            if (op == "and" and not a) or (op == "or" and a):
                return a
            b = value(right, env, done)
            if not isinstance(b, bool):
                raise Fail("and, or need booleans")
            return b
        case Binary(op=op, left=left, right=right):
            return _arithmetic(op, value(left, env, done), value(right, env, done))
        case Compare(op=op, left=left, right=right):
            return _compare(op, value(left, env, done), value(right, env, done))
        case Call(func=Name(name="len"), args=(arg,)):
            v = value(arg, env, done)
            if not isinstance(v, str | list | dict):
                raise Fail("len needs a list, a string or a record")
            return len(v)
    raise NotImplementedError(type(expr).__name__)


def _arithmetic(op: str, a: object, b: object) -> object:
    if op == "+" and isinstance(a, str) and isinstance(b, str):
        return a + b
    if not (_number(a) and _number(b)):
        raise Fail("arithmetic needs numbers")
    x, y = a, b
    if op == "+":
        return x + y  # type: ignore[operator]
    if op == "-":
        return x - y  # type: ignore[operator]
    if op == "*":
        return x * y  # type: ignore[operator]
    if y == 0:
        raise Fail("division by zero")
    if op == "/":
        return x / y  # type: ignore[operator]
    return x % y  # type: ignore[operator]


def _compare(op: str, a: object, b: object) -> bool:
    if op == "==":
        return equal(a, b)
    if op == "!=":
        return not equal(a, b)
    if op == "in":
        if isinstance(b, list):
            return any(equal(a, item) for item in b)
        if isinstance(b, str) and isinstance(a, str):
            return a in b
        if isinstance(b, dict) and isinstance(a, str):
            return a in b
        raise Fail("in needs a list, a string or a record")
    if (_number(a) and _number(b)) or (isinstance(a, str) and isinstance(b, str)):
        x: Any = a
        y: Any = b
        return bool({"<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y}[op])
    raise Fail("ordering needs two numbers or two strings")


# -- one execution -----------------------------------------------------------------------------
def run(
    plan: Plan,
    values: Mapping[str, object],
    failing: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """The state of every step. ``values`` has ``inputs.x`` and ``step.field`` for every field
    that a guard may read; a step in ``failing`` fails if it gets to run."""
    states: dict[str, str] = {}
    done: set[str] = set()
    for step in plan.steps:
        blocked = join_result(step.join.kind, step.join.k, [states[d] for d in step.needs])
        if blocked is not None:
            states[step.id] = blocked
            continue
        if step.when is not None:
            try:
                answer = value(step.when, values, frozenset(done))
            except Fail:
                states[step.id] = FAILED
                continue
            if not isinstance(answer, bool):
                states[step.id] = FAILED
                continue
            if not answer:
                states[step.id] = SKIPPED
                continue
        if step.id in failing:
            states[step.id] = FAILED
        else:
            states[step.id] = SUCCEEDED
            done.add(step.id)
    return states


@dataclass(frozen=True)
class Execution:
    values: Mapping[str, object]
    failing: frozenset[str]
    states: Mapping[str, str]


def may_fail(plan: Plan) -> list[str]:
    return [s.id for s in plan.steps if s.on_failure == "continue"]


def executions(
    plan: Plan,
    grid: Mapping[str, Sequence[object]],
    failures: bool = True,
    limit: int | None = None,
) -> Iterator[Execution]:
    """Every combination of the values of ``grid`` (``inputs.x``, ``step.field`` -> candidates)
    and, when ``failures`` is true, of the steps that may fail."""
    names = sorted(grid)
    subsets: list[frozenset[str]] = [frozenset()]
    if failures:
        flaky = may_fail(plan)
        subsets = [
            frozenset(c) for n in range(len(flaky) + 1) for c in itertools.combinations(flaky, n)
        ]
    count = 0
    for combo in itertools.product(*(grid[n] for n in names)):
        values = dict(zip(names, combo, strict=True))
        for failing in subsets:
            yield Execution(values, failing, run(plan, values, failing))
            count += 1
            if limit is not None and count >= limit:
                return


# -- the longest chain that really ran (spec 11) ----------------------------------------------
def ancestors(plan: Plan) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for step in plan.steps:  # topological order
        found: set[str] = set()
        for d in step.needs:
            found |= out[d] | {d}
        out[step.id] = found
    return out


def longest_chain(plan: Plan, ran: set[str], durations: Mapping[str, float]) -> float:
    """The length of the longest sequence of steps of ``ran``, each an ancestor of the next."""
    above = ancestors(plan)
    best: dict[str, float] = {}
    for step in plan.steps:
        if step.id in ran:
            best[step.id] = durations.get(step.id, 1.0) + max(
                (best[a] for a in above[step.id] if a in ran), default=0.0
            )
    return max(best.values(), default=0.0)
