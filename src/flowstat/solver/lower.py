"""From an expression to a predicate (C6, dsl-spec 3.2).

``lower`` reads the fragment: comparisons of a variable with a literal, sums and differences with a
literal, differences of two numeric variables, membership in a list literal, boolean variables, and
and/or/not over them. Everything else becomes one ``Opaque`` atom. The caller says which references
are variables (and what their domain is) with a resolver; the solver knows nothing about steps.

The rules follow the evaluator (dsl-spec 3.1): ``==`` accepts any two values and ``true`` is not
``1``, so a comparison of different types is decided here; an ordering of different types would
fail at run time, so it is opaque.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction

from flowstat.dsl.ast import Binary, Compare, Expr, ListLit, Literal, Ref, Unary, to_source
from flowstat.solver.domains import Domain, as_number
from flowstat.solver.predicate import (
    FALSE,
    TRUE,
    And,
    Cmp,
    Diff,
    Member,
    Not,
    Opaque,
    Or,
    Pred,
    Truth,
)

_SWAP = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "==": "==", "!=": "!="}
_ORDERINGS = ("<", "<=", ">", ">=")


@dataclass(frozen=True, slots=True)
class Var:
    name: str
    domain: Domain


Resolver = Callable[[Ref], Var | None]


def lower(expr: Expr, resolve: Resolver) -> Pred:
    match expr:
        case Literal(value=bool() as value):
            return TRUE if value else FALSE
        case Ref():
            var = resolve(expr)
            if var is not None and var.domain.kind == "bool":
                return Truth(var.name)
        case Unary(op="not", operand=operand):
            return Not(lower(operand, resolve))
        case Binary(op="and", left=left, right=right):
            return And((lower(left, resolve), lower(right, resolve)))
        case Binary(op="or", left=left, right=right):
            return Or((lower(left, resolve), lower(right, resolve)))
        case Compare(op="in", left=left, right=right):
            return _membership(expr, left, right, resolve)
        case Compare(op=op, left=left, right=right):
            return _compare(expr, op, left, right, resolve)
    return Opaque(to_source(expr))


# -- affine expressions -----------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _Affine:
    coefficients: dict[str, int]
    constant: Fraction
    variables: dict[str, Var]

    def combined(self, other: _Affine, sign: int) -> _Affine:
        coefficients = dict(self.coefficients)
        for name, k in other.coefficients.items():
            coefficients[name] = coefficients.get(name, 0) + sign * k
        return _Affine(
            {n: k for n, k in coefficients.items() if k},
            self.constant + sign * other.constant,
            {**self.variables, **other.variables},
        )

    def negated(self) -> _Affine:
        return _Affine(
            {n: -k for n, k in self.coefficients.items()}, -self.constant, self.variables
        )


def _affine(expr: Expr, resolve: Resolver) -> _Affine | None:
    match expr:
        case Literal(value=value):
            number = as_number(value)
            return None if number is None else _Affine({}, number, {})
        case Ref():
            var = resolve(expr)
            if var is None or var.domain.kind not in ("int", "real"):
                return None
            return _Affine({var.name: 1}, Fraction(0), {var.name: var})
        case Unary(op="-", operand=operand):
            inner = _affine(operand, resolve)
            return None if inner is None else inner.negated()
        case Binary(op="+" | "-" as op, left=left, right=right):
            a, b = _affine(left, resolve), _affine(right, resolve)
            if a is None or b is None:
                return None
            return a.combined(b, 1 if op == "+" else -1)
    return None


# -- comparisons ------------------------------------------------------------------------------
def _compare(expr: Expr, op: str, left: Expr, right: Expr, resolve: Resolver) -> Pred:
    opaque = Opaque(to_source(expr))
    if _is_null(left) or _is_null(right):
        return opaque
    a, b = _affine(left, resolve), _affine(right, resolve)
    if a is not None and b is not None:
        return _linear(a.combined(b, -1), op, opaque)
    if isinstance(left, Literal) and isinstance(right, Literal):
        decided = _literals(left.value, op, right.value)
        return opaque if decided is None else (TRUE if decided else FALSE)
    if isinstance(right, Ref) and isinstance(left, Literal):
        left, right, op = right, left, _SWAP[op]
    if isinstance(left, Ref) and isinstance(right, Literal):
        var = resolve(left)
        if var is not None:
            return _variable_and_literal(var, op, right.value, opaque)
    return opaque


def _is_null(expr: Expr) -> bool:
    return isinstance(expr, Literal) and expr.value is None


def _linear(a: _Affine, op: str, opaque: Opaque) -> Pred:
    """``sum + constant op 0`` with at most two variables of coefficient one."""
    k = a.constant
    names = sorted(a.coefficients)
    coefficients = [a.coefficients[n] for n in names]
    if not names:
        return TRUE if _literals(k, op, Fraction(0)) else FALSE
    if len(names) == 1 and coefficients[0] == 1:
        return Cmp(names[0], op, -k)
    if len(names) == 1 and coefficients[0] == -1:
        return Cmp(names[0], _SWAP[op], k)
    if len(names) == 2 and sorted(coefficients) == [-1, 1]:
        plus = names[coefficients.index(1)]
        minus = names[coefficients.index(-1)]
        return Diff(plus, minus, op, -k)
    return opaque


def _literals(a: object, op: str, b: object) -> bool | None:
    """The result of comparing two literals as the engine does; ``None`` if it would fail."""
    if op in ("==", "!="):
        same = _equal(a, b)
        return same if op == "==" else not same
    x, y = as_number(a), as_number(b)
    if x is not None and y is not None:
        return {"<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y}[op]
    if isinstance(a, str) and isinstance(b, str):
        return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
    return None


def _equal(a: object, b: object) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    x, y = as_number(a), as_number(b)
    if x is not None and y is not None:
        return x == y
    return isinstance(a, str) and isinstance(b, str) and a == b


def _variable_and_literal(var: Var, op: str, literal: object, opaque: Opaque) -> Pred:
    kind = var.domain.kind
    if kind in ("int", "real"):
        # a number against a literal that is not a number: only equality can be decided
        return _different_types(op, opaque)
    if kind == "bool":
        if isinstance(literal, bool) and op in ("==", "!="):
            return Truth(var.name, negated=(literal is False) == (op == "=="))
        return opaque if op in _ORDERINGS else _different_types(op, opaque)
    if isinstance(literal, str):
        return Cmp(var.name, op, literal) if op in ("==", "!=") else opaque
    return opaque if op in _ORDERINGS else _different_types(op, opaque)


def _different_types(op: str, opaque: Opaque) -> Pred:
    if op == "==":
        return FALSE
    if op == "!=":
        return TRUE
    return opaque


def _membership(expr: Expr, left: Expr, right: Expr, resolve: Resolver) -> Pred:
    opaque = Opaque(to_source(expr))
    if not isinstance(right, ListLit) or not isinstance(left, Ref):
        return opaque
    var = resolve(left)
    if var is None or not all(isinstance(i, Literal) for i in right.items):
        return opaque
    items = [i.value for i in right.items if isinstance(i, Literal)]
    kind = var.domain.kind
    if kind in ("int", "real"):
        numbers = tuple(n for n in (as_number(v) for v in items) if n is not None)
        return Member(var.name, numbers) if numbers else FALSE
    if kind == "bool":
        flags = {v for v in items if isinstance(v, bool)}
        if flags == {True, False}:
            return TRUE
        if flags:
            return Truth(var.name, negated=flags == {False})
        return FALSE
    strings = tuple(v for v in items if isinstance(v, str))
    return Member(var.name, strings) if strings else FALSE
