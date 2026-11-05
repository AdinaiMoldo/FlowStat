"""Predicate tree (C6): atoms, negation normal form, DNF with a clause cap, evaluation.

A predicate is what the solver sees of a guard: atoms on variables, joined by and, or and not. An
``Opaque`` atom stands for anything outside the fragment; it is never decided. Evaluation is
three-valued (Kleene): an opaque atom gives ``None``, and ``false and ?`` is still ``false``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction

from flowstat.solver.domains import as_number

_FLIP = {"<": ">=", "<=": ">", ">": "<=", ">=": "<", "==": "!=", "!=": "=="}


@dataclass(frozen=True, slots=True)
class Cmp:
    """``var op const`` with ``const`` a Fraction (numbers) or a str."""

    var: str
    op: str
    const: Fraction | str


@dataclass(frozen=True, slots=True)
class Diff:
    """``x - y op const`` for two numeric variables."""

    x: str
    y: str
    op: str
    const: Fraction


@dataclass(frozen=True, slots=True)
class Member:
    """``var in [consts]`` (``var not in`` when negated)."""

    var: str
    consts: tuple[Fraction | str, ...]
    negated: bool = False


@dataclass(frozen=True, slots=True)
class Truth:
    """A boolean variable (``not var`` when negated)."""

    var: str
    negated: bool = False


@dataclass(frozen=True, slots=True)
class Opaque:
    """An atom the solver does not understand; ``text`` is its source, for messages."""

    text: str


@dataclass(frozen=True, slots=True)
class Const:
    value: bool


@dataclass(frozen=True, slots=True)
class And:
    items: tuple[Pred, ...]


@dataclass(frozen=True, slots=True)
class Or:
    items: tuple[Pred, ...]


@dataclass(frozen=True, slots=True)
class Not:
    item: Pred


Atom = Cmp | Diff | Member | Truth | Opaque
Pred = Atom | Const | And | Or | Not
Clause = tuple[Atom, ...]

TRUE = Const(True)
FALSE = Const(False)


# -- reading ----------------------------------------------------------------------------------
def show(p: Pred) -> str:
    match p:
        case Cmp(var=var, op=op, const=const):
            return f"{var} {op} {_const(const)}"
        case Diff(x=x, y=y, op=op, const=const):
            return f"{x} - {y} {op} {_const(const)}"
        case Member(var=var, consts=consts, negated=negated):
            body = ", ".join(_const(c) for c in consts)
            return f"{var} {'not in' if negated else 'in'} [{body}]"
        case Truth(var=var, negated=negated):
            return f"not {var}" if negated else var
        case Opaque(text=text):
            return f"?({text})"
        case Const(value=value):
            return "true" if value else "false"
        case And(items=items):
            return "(" + " and ".join(show(i) for i in items) + ")"
        case Or(items=items):
            return "(" + " or ".join(show(i) for i in items) + ")"
        case Not(item=item):
            return f"not {show(item)}"


def _const(c: Fraction | str) -> str:
    return repr(c) if isinstance(c, str) else str(c)


def variables(p: Pred) -> set[str]:
    match p:
        case Cmp(var=v) | Member(var=v) | Truth(var=v):
            return {v}
        case Diff(x=x, y=y):
            return {x, y}
        case And(items=items) | Or(items=items):
            return set().union(*(variables(i) for i in items)) if items else set()
        case Not(item=item):
            return variables(item)
        case _:
            return set()


def has_opaque(p: Pred) -> bool:
    match p:
        case Opaque():
            return True
        case And(items=items) | Or(items=items):
            return any(has_opaque(i) for i in items)
        case Not(item=item):
            return has_opaque(item)
        case _:
            return False


# -- normal forms -----------------------------------------------------------------------------
def nnf(p: Pred, negate: bool = False) -> Pred:
    """Negation normal form: negations are pushed to the atoms and absorbed by them."""
    match p:
        case Const(value=value):
            return Const(value != negate)
        case Cmp(var=var, op=op, const=const):
            return Cmp(var, _FLIP[op] if negate else op, const)
        case Diff(x=x, y=y, op=op, const=const):
            op = _FLIP[op] if negate else op
            if op == "!=":
                return Or((Diff(x, y, "<", const), Diff(x, y, ">", const)))
            return Diff(x, y, op, const)
        case Member(var=var, consts=consts, negated=negated):
            return Member(var, consts, negated != negate)
        case Truth(var=var, negated=negated):
            return Truth(var, negated != negate)
        case Opaque():
            return p
        case Not(item=item):
            return nnf(item, not negate)
        case And(items=items):
            kids = tuple(nnf(i, negate) for i in items)
            return Or(kids) if negate else And(kids)
        case Or(items=items):
            kids = tuple(nnf(i, negate) for i in items)
            return And(kids) if negate else Or(kids)


def dnf(p: Pred, cap: int) -> list[Clause] | None:
    """Clauses whose disjunction is ``p``; ``None`` when more than ``cap`` clauses are needed.

    The cap is checked after every step, so a product that would be huge is never built.
    """
    return _expand(nnf(p), cap)


def _expand(p: Pred, cap: int) -> list[Clause] | None:
    match p:
        case Const(value=value):
            return [()] if value else []
        case Or(items=items):
            out: list[Clause] = []
            for item in items:
                part = _expand(item, cap)
                if part is None:
                    return None
                out.extend(part)
                if len(out) > cap:
                    return None
            return out
        case And(items=items):
            acc: list[Clause] = [()]
            for item in items:
                part = _expand(item, cap)
                if part is None:
                    return None
                acc = [_join(a, b) for a in acc for b in part]
                if len(acc) > cap:
                    return None
            return acc
        case _:
            return [(p,)]  # type: ignore[list-item]  # an atom


def _join(a: Clause, b: Clause) -> Clause:
    return a + tuple(x for x in b if x not in a)


# -- evaluation -------------------------------------------------------------------------------
def holds(p: Pred, env: Mapping[str, object]) -> bool | None:
    """Truth of ``p`` for the values in ``env``; ``None`` when an opaque atom decides it."""
    match p:
        case Const(value=value):
            return value
        case Opaque():
            return None
        case Not(item=item):
            inner = holds(item, env)
            return None if inner is None else not inner
        case And(items=items):
            unknown = False
            for item in items:
                got = holds(item, env)
                if got is False:
                    return False
                unknown = unknown or (got is None)
            return None if unknown else True
        case Or(items=items):
            unknown = False
            for item in items:
                got = holds(item, env)
                if got is True:
                    return True
                unknown = unknown or (got is None)
            return None if unknown else False
        case Truth(var=var, negated=negated):
            current = env[var]
            return None if not isinstance(current, bool) else current != negated
        case Cmp(var=var, op=op, const=const):
            return _compare(env[var], op, const)
        case Diff(x=x, y=y, op=op, const=const):
            a, b = as_number(env[x]), as_number(env[y])
            if a is None or b is None:
                return None
            return _compare(a - b, op, const)
        case Member(var=var, consts=consts, negated=negated):
            current = env[var]
            found = any(_same(current, c) for c in consts)
            return found != negated


def _same(value: object, const: Fraction | str) -> bool:
    if isinstance(const, str):
        return isinstance(value, str) and value == const
    return as_number(value) == const


def _compare(value: object, op: str, const: Fraction | str) -> bool | None:
    if op == "==":
        return _same(value, const)
    if op == "!=":
        return not _same(value, const)
    v = as_number(value)
    if isinstance(const, str) or v is None:
        return None  # the engine would fail: ordering needs two numbers
    return {"<": v < const, "<=": v <= const, ">": v > const, ">=": v >= const}[op]
