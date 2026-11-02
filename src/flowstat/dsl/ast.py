"""Expression tree (C1, level 1) shared by the parser, the analyses and the engine evaluator."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Node:
    line: int
    column: int


@dataclass(frozen=True, slots=True)
class Literal(Node):
    value: object  # int, float, str, bool or None


@dataclass(frozen=True, slots=True)
class Name(Node):
    name: str


@dataclass(frozen=True, slots=True)
class PathPart:
    """One step of a reference path.

    kind is "field" (``.x`` or the first name), "var" (``[region]``), "index" (``[0]``)
    or "star" (``[*]``).
    """

    kind: str
    value: str | int


@dataclass(frozen=True, slots=True)
class Ref(Node):
    path: tuple[PathPart, ...]


@dataclass(frozen=True, slots=True)
class Unary(Node):
    op: str  # "-" or "not"
    operand: Expr


@dataclass(frozen=True, slots=True)
class Binary(Node):
    op: str  # + - * / % and or
    left: Expr
    right: Expr


@dataclass(frozen=True, slots=True)
class Compare(Node):
    op: str  # == != < <= > >= in
    left: Expr
    right: Expr


@dataclass(frozen=True, slots=True)
class Call(Node):
    func: Expr
    args: tuple[Expr, ...]


@dataclass(frozen=True, slots=True)
class Attr(Node):
    obj: Expr
    name: str


@dataclass(frozen=True, slots=True)
class Index(Node):
    obj: Expr
    index: Expr


@dataclass(frozen=True, slots=True)
class ErrorNode(Node):
    message: str


Expr = Literal | Name | Ref | Unary | Binary | Compare | Call | Attr | Index | ErrorNode


def _ref_text(ref: Ref) -> str:
    out = ["${"]
    for i, part in enumerate(ref.path):
        if part.kind == "field":
            out.append(("." if i else "") + str(part.value))
        elif part.kind == "star":
            out.append("[*]")
        else:
            out.append(f"[{part.value}]")
    out.append("}")
    return "".join(out)


def _literal_text(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        text = format(Decimal(repr(value)), "f")
        return text if "." in text else text + ".0"
    if isinstance(value, str):
        escaped = (
            value.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\n", "\\n")
            .replace("\t", "\\t")
            .replace("\r", "\\r")
        )
        return f"'{escaped}'"
    return str(value)


def to_source(expr: Expr) -> str:
    """Fully parenthesised source text; parsing it again gives the same tree (up to positions)."""
    match expr:
        case Literal(value=value):
            return _literal_text(value)
        case Name(name=name):
            return name
        case Ref():
            return _ref_text(expr)
        case Unary(op=op, operand=operand):
            sep = " " if op == "not" else ""
            return f"({op}{sep}{to_source(operand)})"
        case Binary(op=op, left=left, right=right) | Compare(op=op, left=left, right=right):
            return f"({to_source(left)} {op} {to_source(right)})"
        case Call(func=func, args=args):
            return f"{to_source(func)}({', '.join(to_source(a) for a in args)})"
        case Attr(obj=obj, name=name):
            return f"{to_source(obj)}.{name}"
        case Index(obj=obj, index=index):
            return f"{to_source(obj)}[{to_source(index)}]"
        case ErrorNode():
            return "<error>"


def dump(expr: Expr) -> str:
    """S-expression of the structure without positions; used by tests and for debugging."""
    match expr:
        case Literal(value=value):
            return _literal_text(value)
        case Name(name=name):
            return name
        case Ref():
            return _ref_text(expr)
        case Unary(op=op, operand=operand):
            return f"({op} {dump(operand)})"
        case Binary(op=op, left=left, right=right) | Compare(op=op, left=left, right=right):
            return f"({op} {dump(left)} {dump(right)})"
        case Call(func=func, args=args):
            return "(call " + " ".join([dump(func), *(dump(a) for a in args)]) + ")"
        case Attr(obj=obj, name=name):
            return f"(. {dump(obj)} {name})"
        case Index(obj=obj, index=index):
            return f"(idx {dump(obj)} {dump(index)})"
        case ErrorNode():
            return "(error)"
