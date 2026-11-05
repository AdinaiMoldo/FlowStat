"""Per-variable domains (C6): interval sets over exact numbers, finite and cofinite string sets.

A clause of a predicate restricts each variable on its own (``x > 3``, ``s != 'a'``); the domain of
a variable is what is left after all such restrictions. Numbers are ``Fraction``s, so a constant
such as ``0.1`` is exact. Over the integers every piece is a closed interval with integer ends
(``x < 6`` becomes ``x <= 5``), which keeps the later steps simple and exact.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

_KINDS = ("int", "real", "bool", "str")


@dataclass(frozen=True, slots=True)
class Domain:
    """What is known about a variable before any guard is read.

    ``kind`` is ``int``, ``real``, ``bool`` or ``str``. ``values`` limits it to a list of allowed
    constants (an enumeration, or the ``values`` of an input); ``lo`` and ``hi`` are inclusive
    bounds of a number.
    """

    kind: str
    values: tuple[object, ...] | None = None
    lo: Fraction | None = None
    hi: Fraction | None = None

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise ValueError(f"unknown kind {self.kind!r}")

    @staticmethod
    def integer(
        lo: int | Fraction | None = None,
        hi: int | Fraction | None = None,
        values: tuple[int, ...] | None = None,
    ) -> Domain:
        return Domain("int", values, _frac(lo), _frac(hi))

    @staticmethod
    def real(
        lo: int | Fraction | None = None,
        hi: int | Fraction | None = None,
        values: tuple[int | Fraction, ...] | None = None,
    ) -> Domain:
        return Domain("real", values, _frac(lo), _frac(hi))

    @staticmethod
    def boolean() -> Domain:
        return Domain("bool")

    @staticmethod
    def string() -> Domain:
        return Domain("str")

    @staticmethod
    def enum(*values: str) -> Domain:
        if not values:
            raise ValueError("an enumeration needs at least one value")
        return Domain("str", tuple(values))


def _frac(value: int | Fraction | None) -> Fraction | None:
    return None if value is None else Fraction(value)


def as_number(value: object) -> Fraction | None:
    """The exact value of an int, a float (read as its shortest decimal) or a Fraction.

    A bool is not a number (``true`` is not ``1``, dsl-spec 3.1); anything else gives ``None``.
    """
    if isinstance(value, bool) or not isinstance(value, int | float | Fraction):
        return None
    return Fraction(repr(value)) if isinstance(value, float) else Fraction(value)


# -- numbers ----------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Interval:
    lo: Fraction | None  # None: no lower end
    lo_closed: bool
    hi: Fraction | None
    hi_closed: bool

    def contains(self, v: Fraction) -> bool:
        if self.lo is not None and (v < self.lo or (v == self.lo and not self.lo_closed)):
            return False
        return self.hi is None or v < self.hi or (v == self.hi and self.hi_closed)

    @property
    def empty(self) -> bool:
        if self.lo is None or self.hi is None:
            return False
        return self.lo > self.hi or (self.lo == self.hi and not (self.lo_closed and self.hi_closed))


_LINE = Interval(None, False, None, False)


def _meet(a: Interval, b: Interval) -> Interval:
    if a.lo is None:
        lo, lc = b.lo, b.lo_closed
    elif b.lo is None or a.lo > b.lo:
        lo, lc = a.lo, a.lo_closed
    elif b.lo > a.lo:
        lo, lc = b.lo, b.lo_closed
    else:
        lo, lc = a.lo, a.lo_closed and b.lo_closed
    if a.hi is None:
        hi, hc = b.hi, b.hi_closed
    elif b.hi is None or a.hi < b.hi:
        hi, hc = a.hi, a.hi_closed
    elif b.hi < a.hi:
        hi, hc = b.hi, b.hi_closed
    else:
        hi, hc = a.hi, a.hi_closed and b.hi_closed
    return Interval(lo, lc, hi, hc)


def _integral(p: Interval) -> Interval:
    """The same integers, written with closed integer ends."""
    lo, hi = p.lo, p.hi
    if lo is not None:
        lo = Fraction(math.ceil(lo) if p.lo_closed else math.floor(lo) + 1)
    if hi is not None:
        hi = Fraction(math.floor(hi) if p.hi_closed else math.ceil(hi) - 1)
    return Interval(lo, lo is not None, hi, hi is not None)


@dataclass(frozen=True, slots=True)
class NumberSet:
    """A union of disjoint intervals, in increasing order."""

    pieces: tuple[Interval, ...]
    integer: bool

    @staticmethod
    def of(domain: Domain) -> NumberSet:
        integer = domain.kind == "int"
        if domain.values is not None:
            points = sorted({n for n in map(as_number, domain.values) if n is not None})
            base = NumberSet(tuple(Interval(p, True, p, True) for p in points), integer)
            base = NumberSet._clean(base.pieces, integer)
        else:
            base = NumberSet._clean((_LINE,), integer)
        if domain.lo is not None:
            base = base.restrict(">=", domain.lo)
        if domain.hi is not None:
            base = base.restrict("<=", domain.hi)
        return base

    @staticmethod
    def _clean(pieces: tuple[Interval, ...] | list[Interval], integer: bool) -> NumberSet:
        out = []
        for p in pieces:
            q = _integral(p) if integer else p
            if not q.empty:
                out.append(q)
        return NumberSet(tuple(out), integer)

    @property
    def empty(self) -> bool:
        return not self.pieces

    def contains(self, v: Fraction | int) -> bool:
        value = Fraction(v)
        if self.integer and value.denominator != 1:
            return False
        return any(p.contains(value) for p in self.pieces)

    def restrict(self, op: str, c: Fraction) -> NumberSet:
        if op == "!=":
            return self._without(c)
        cut = {
            "<": Interval(None, False, c, False),
            "<=": Interval(None, False, c, True),
            ">": Interval(c, False, None, False),
            ">=": Interval(c, True, None, False),
            "==": Interval(c, True, c, True),
        }[op]
        return self._clean([_meet(p, cut) for p in self.pieces], self.integer)

    def restrict_in(self, consts: tuple[Fraction, ...], negated: bool) -> NumberSet:
        if negated:
            out = self
            for c in consts:
                out = out._without(c)
            return out
        points = sorted({c for c in consts if self.contains(c)})
        return NumberSet(tuple(Interval(p, True, p, True) for p in points), self.integer)

    def _without(self, c: Fraction) -> NumberSet:
        if self.integer and c.denominator != 1:
            return self
        out: list[Interval] = []
        for p in self.pieces:
            if p.contains(c):
                out.append(Interval(p.lo, p.lo_closed, c, False))
                out.append(Interval(c, False, p.hi, p.hi_closed))
            else:
                out.append(p)
        return self._clean(out, self.integer)

    def sample(self) -> Fraction:
        """A member, chosen the same way every time: zero if it is there, else near an end."""
        if any(p.contains(Fraction(0)) for p in self.pieces):
            return Fraction(0)
        p = self.pieces[0]
        if p.lo is not None:
            if p.lo_closed:
                return p.lo
            return p.lo + 1 if p.hi is None else (p.lo + p.hi) / 2
        assert p.hi is not None
        return p.hi if p.hi_closed else p.hi - 1


# -- strings ----------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class StringSet:
    """A finite set (an enumeration) or a cofinite one (any string except some)."""

    allowed: frozenset[str] | None  # None: every string
    excluded: frozenset[str]

    @staticmethod
    def of(domain: Domain) -> StringSet:
        if domain.values is None:
            return StringSet(None, frozenset())
        return StringSet(frozenset(str(v) for v in domain.values), frozenset())

    @property
    def empty(self) -> bool:
        return self.allowed is not None and not (self.allowed - self.excluded)

    def values(self) -> frozenset[str]:
        assert self.allowed is not None
        return self.allowed - self.excluded

    def contains(self, v: str) -> bool:
        return (self.allowed is None or v in self.allowed) and v not in self.excluded

    def restrict(self, op: str, c: str) -> StringSet:
        if op == "==":
            allowed = frozenset({c}) if self.allowed is None else self.allowed & {c}
            return StringSet(allowed, self.excluded)
        return StringSet(self.allowed, self.excluded | {c})

    def restrict_in(self, consts: tuple[str, ...], negated: bool) -> StringSet:
        if negated:
            return StringSet(self.allowed, self.excluded | set(consts))
        allowed = frozenset(consts) if self.allowed is None else self.allowed & set(consts)
        return StringSet(allowed, self.excluded)

    def sample(self) -> str:
        if self.allowed is not None:
            return sorted(self.allowed - self.excluded)[0]
        candidate, n = "x", 0
        while candidate in self.excluded:
            n += 1
            candidate = f"x{n}"
        return candidate


# -- booleans ---------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class BoolSet:
    members: frozenset[bool]

    @staticmethod
    def of(domain: Domain) -> BoolSet:
        if domain.values is None:
            return BoolSet(frozenset({False, True}))
        return BoolSet(frozenset(bool(v) for v in domain.values))

    @property
    def empty(self) -> bool:
        return not self.members

    def restrict(self, value: bool) -> BoolSet:
        return BoolSet(self.members & {value})

    def sample(self) -> bool:
        return False not in self.members
