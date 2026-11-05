"""Expression evaluator (C3): the runtime meaning of expressions, dsl-spec 3.1.

A tree walk over the AST of our own parser. Strict and small on purpose: no implicit conversions,
no truthiness, and a missing field is an error, never ``null``. Errors are reported as
``EvalError`` with a code E-RUN-001 to E-RUN-008 and the position of the part of the expression.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from flowstat.dsl.ast import (
    Attr,
    Binary,
    Call,
    Compare,
    ErrorNode,
    Expr,
    Index,
    Literal,
    Name,
    Node,
    PathPart,
    Ref,
    Unary,
)


class EvalError(Exception):
    def __init__(self, code: str, message: str, line: int, column: int) -> None:
        super().__init__(f"{code} {message}")
        self.code = code
        self.message = message
        self.line = line
        self.column = column


@dataclass(frozen=True)
class EvalContext:
    inputs: Mapping[str, object]
    outputs: Mapping[str, Mapping[str, object]]  # step id -> output; only steps that SUCCEEDED
    instances: Mapping[str, tuple[str, ...]]  # foreach step -> its instances, declaration order


def _fail(code: str, message: str, node: Node) -> EvalError:
    return EvalError(code, message, node.line, node.column)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _kind(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    return "record"


# -- references ----------------------------------------------------------------------------------
def _normalize(path: tuple[PathPart, ...]) -> list[PathPart]:
    """Merge ``name[var]`` into the name of an instance (the compiler already does this)."""
    out: list[PathPart] = []
    for part in path:
        if part.kind == "var" and out and out[-1].kind == "field":
            out[-1] = PathPart("field", f"{out[-1].value}[{part.value}]")
        else:
            out.append(part)
    return out


def _field(value: object, name: str, node: Node) -> object:
    if isinstance(value, dict) and name in value:
        return value[name]
    if value is None:
        raise _fail("E-RUN-003", f"cannot read '{name}' of null", node)
    if isinstance(value, dict):
        raise _fail("E-RUN-003", f"there is no field '{name}'", node)
    raise _fail("E-RUN-003", f"a {_kind(value)} has no field '{name}'", node)


def _walk(value: object, parts: list[PathPart], node: Node) -> object:
    for part in parts:
        if part.kind != "field":
            raise _fail("E-RUN-001", "unresolved reference", node)
        value = _field(value, str(part.value), node)
    return value


def _eval_ref(ref: Ref, ctx: EvalContext) -> object:
    parts = _normalize(ref.path)
    if not parts or parts[0].kind != "field":
        raise _fail("E-RUN-001", "unresolved reference", ref)
    head = str(parts[0].value)
    rest = parts[1:]
    if head == "inputs":
        if not rest or rest[0].kind != "field":
            raise _fail("E-RUN-002", "'inputs' needs the name of an input", ref)
        name = str(rest[0].value)
        if name not in ctx.inputs:
            raise _fail("E-RUN-002", f"there is no input '{name}'", ref)
        return _walk(ctx.inputs[name], rest[1:], ref)
    if rest and rest[0].kind == "star":
        items = [ctx.outputs[i] for i in ctx.instances.get(head, ()) if i in ctx.outputs]
        return [_walk(dict(item), rest[1:], ref) for item in items]
    if head not in ctx.outputs:
        raise _fail("E-RUN-002", f"step '{head}' has no output (it did not succeed)", ref)
    return _walk(dict(ctx.outputs[head]), rest, ref)


# -- operators -----------------------------------------------------------------------------------
def _equal(a: object, b: object) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if _is_number(a) and _is_number(b):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_equal(a[k], b[k]) for k in a)
    if type(a) is not type(b):
        return False
    return bool(a == b)


def _arith(op: str, a: object, b: object, node: Node) -> object:
    if op == "+" and isinstance(a, str) and isinstance(b, str):
        return a + b
    if not (_is_number(a) and _is_number(b)):
        raise _fail("E-RUN-005", f"'{op}' does not fit {_kind(a)} and {_kind(b)}", node)
    assert isinstance(a, int | float) and isinstance(b, int | float)
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if b == 0:
        raise _fail("E-RUN-004", "division by zero", node)
    return a / b if op == "/" else a % b


def _order(op: str, a: object, b: object, node: Node) -> bool:
    both_numbers = _is_number(a) and _is_number(b)
    both_strings = isinstance(a, str) and isinstance(b, str)
    if not (both_numbers or both_strings):
        raise _fail("E-RUN-005", f"'{op}' does not fit {_kind(a)} and {_kind(b)}", node)
    assert isinstance(a, int | float | str) and isinstance(b, int | float | str)
    if op == "<":
        return bool(a < b)  # type: ignore[operator]
    if op == "<=":
        return bool(a <= b)  # type: ignore[operator]
    if op == ">":
        return bool(a > b)  # type: ignore[operator]
    return bool(a >= b)  # type: ignore[operator]


def _contains(item: object, container: object, node: Node) -> bool:
    if isinstance(container, list):
        return any(_equal(item, x) for x in container)
    if isinstance(container, str) and isinstance(item, str):
        return item in container
    if isinstance(container, dict) and isinstance(item, str):
        return item in container
    raise _fail("E-RUN-005", f"'in' does not fit {_kind(item)} and {_kind(container)}", node)


def _bool(value: object, node: Node) -> bool:
    if not isinstance(value, bool):
        raise _fail("E-RUN-005", f"a boolean is needed, found {_kind(value)}", node)
    return value


# -- built-in functions --------------------------------------------------------------------------
def _one(args: list[object], name: str, node: Node) -> object:
    if len(args) != 1:
        raise _fail("E-RUN-007", f"{name} takes exactly one argument", node)
    return args[0]


def _len(args: list[object], node: Node) -> object:
    value = _one(args, "len", node)
    if isinstance(value, list | str | dict):
        return len(value)
    raise _fail("E-RUN-005", f"len does not fit {_kind(value)}", node)


def _numbers(value: object, name: str, node: Node) -> list[int | float]:
    if not isinstance(value, list) or not all(_is_number(x) for x in value):
        raise _fail("E-RUN-005", f"{name} needs a list of numbers", node)
    return [x for x in value if isinstance(x, int | float)]


def _sum(args: list[object], node: Node) -> object:
    return sum(_numbers(_one(args, "sum", node), "sum", node))


def _extreme(name: str) -> Callable[[list[object], Node], object]:
    def apply(args: list[object], node: Node) -> object:
        value = _one(args, name, node)
        if not isinstance(value, list):
            raise _fail("E-RUN-005", f"{name} needs a list", node)
        if not value:
            raise _fail("E-RUN-007", f"{name} of an empty list", node)
        choose = min if name == "min" else max
        if all(_is_number(x) for x in value):
            return choose(x for x in value if isinstance(x, int | float))
        if all(isinstance(x, str) for x in value):
            return choose(x for x in value if isinstance(x, str))
        raise _fail("E-RUN-005", f"{name} needs numbers only or strings only", node)

    return apply


def _count(args: list[object], node: Node) -> object:
    value = _one(args, "count", node)
    if not isinstance(value, list):
        raise _fail("E-RUN-005", f"count does not fit {_kind(value)}", node)
    return sum(1 for x in value if x is not None)


_BUILTINS: dict[str, Callable[[list[object], Node], object]] = {
    "len": _len,
    "sum": _sum,
    "min": _extreme("min"),
    "max": _extreme("max"),
    "count": _count,
}


def _exists(call: Call, ctx: EvalContext) -> bool:
    if len(call.args) != 1:
        raise _fail("E-RUN-007", "exists takes exactly one argument", call)
    arg = call.args[0]
    if not isinstance(arg, Ref | Attr | Index):
        raise _fail("E-RUN-007", "exists needs a reference", call)
    try:
        return evaluate(arg, ctx) is not None
    except EvalError as error:
        if error.code in {"E-RUN-002", "E-RUN-003", "E-RUN-006"}:
            return False
        raise


# -- the walk ------------------------------------------------------------------------------------
def evaluate(expr: Expr, ctx: EvalContext) -> object:
    match expr:
        case Literal(value=value):
            return value
        case Name(name=name):
            raise _fail("E-RUN-001", f"unknown name '{name}'", expr)
        case Ref():
            return _eval_ref(expr, ctx)
        case Unary(op="-", operand=operand):
            value = evaluate(operand, ctx)
            if not _is_number(value):
                raise _fail("E-RUN-005", f"'-' does not fit {_kind(value)}", expr)
            assert isinstance(value, int | float)
            return -value
        case Unary(operand=operand):
            return not _bool(evaluate(operand, ctx), operand)
        case Binary(op="and", left=left, right=right):
            return _bool(evaluate(left, ctx), left) and _bool(evaluate(right, ctx), right)
        case Binary(op="or", left=left, right=right):
            return _bool(evaluate(left, ctx), left) or _bool(evaluate(right, ctx), right)
        case Binary(op=op, left=left, right=right):
            return _arith(op, evaluate(left, ctx), evaluate(right, ctx), expr)
        case Compare(op=op, left=left, right=right):
            a, b = evaluate(left, ctx), evaluate(right, ctx)
            if op == "==":
                return _equal(a, b)
            if op == "!=":
                return not _equal(a, b)
            if op == "in":
                return _contains(a, b, expr)
            return _order(op, a, b, expr)
        case Call(func=Name(name="exists")):
            return _exists(expr, ctx)
        case Call(func=Name(name=name), args=args):
            function = _BUILTINS.get(name)
            if function is None:
                raise _fail("E-RUN-001", f"unknown function '{name}'", expr)
            return function([evaluate(a, ctx) for a in args], expr)
        case Call():
            raise _fail("E-RUN-001", "only built-in functions can be called", expr)
        case Attr(obj=obj, name=name):
            return _field(evaluate(obj, ctx), name, expr)
        case Index(obj=obj, index=index):
            return _index(evaluate(obj, ctx), evaluate(index, ctx), expr)
        case ErrorNode(message=message):
            raise _fail("E-RUN-001", f"invalid expression: {message}", expr)
    raise AssertionError(expr)  # pragma: no cover - the match above covers every node


def _index(value: object, key: object, node: Node) -> object:
    if isinstance(value, list) and isinstance(key, int) and not isinstance(key, bool):
        if 0 <= key < len(value):
            return value[key]
        raise _fail("E-RUN-006", f"index {key} is out of range (length {len(value)})", node)
    if isinstance(value, dict) and isinstance(key, str):
        if key in value:
            return value[key]
        raise _fail("E-RUN-006", f"there is no key '{key}'", node)
    raise _fail("E-RUN-006", f"a {_kind(value)} cannot be indexed by a {_kind(key)}", node)


def evaluate_bool(expr: Expr, ctx: EvalContext) -> bool:
    value = evaluate(expr, ctx)
    if not isinstance(value, bool):
        raise _fail("E-RUN-008", f"a condition must give a boolean, found {_kind(value)}", expr)
    return value


def _text(value: object, node: Node) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    raise _fail("E-RUN-005", f"a {_kind(value)} cannot be joined into text", node)


def render_template(parts: tuple[object, ...], ctx: EvalContext) -> object:
    """Value of a ``with`` argument: one reference or plain value keeps its type, else text."""
    if len(parts) == 1 and not isinstance(parts[0], str):
        only = parts[0]
        return evaluate(only, ctx) if isinstance(only, Ref) else only
    out: list[str] = []
    for part in parts:
        if isinstance(part, Ref):
            out.append(_text(evaluate(part, ctx), part))
        elif isinstance(part, str):
            out.append(part)
        else:
            out.append(str(part))
    return "".join(out)
