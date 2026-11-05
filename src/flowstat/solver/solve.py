"""The solver (C6): satisfiability of a predicate, with three answers.

``SAT`` comes with a witness that the evaluator confirms, ``UNSAT`` is a proof, ``UNKNOWN`` makes
no claim. The predicate becomes a disjunction of clauses (DNF with a cap). A clause is solved in
two parts: restrictions on one variable shrink its domain (``domains.py``), and relations between
two numeric variables go to a difference graph (``diffgraph.py``). A clause with an opaque atom can
be shown unsatisfiable by the rest of it, never satisfiable.
"""

from __future__ import annotations

import enum
import itertools
import math
from dataclasses import dataclass
from fractions import Fraction

from flowstat.solver.diffgraph import Edge, solve_differences
from flowstat.solver.domains import BoolSet, Domain, Interval, NumberSet, StringSet
from flowstat.solver.predicate import (
    And,
    Atom,
    Clause,
    Cmp,
    Diff,
    Member,
    Not,
    Opaque,
    Pred,
    Truth,
    dnf,
    holds,
    variables,
)

MAX_CLAUSES = 64
MAX_COMBINATIONS = 256  # pieces of the variables that meet in a difference, multiplied


class Status(enum.Enum):
    SAT = "sat"
    UNSAT = "unsat"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Result:
    status: Status
    witness: dict[str, object] | None = None


SAT_NO_WITNESS = Result(Status.SAT, {})
_UNSAT = Result(Status.UNSAT)
_UNKNOWN = Result(Status.UNKNOWN)


def check(pred: Pred, domains: dict[str, Domain], *, max_clauses: int = MAX_CLAUSES) -> Result:
    """Is there an assignment inside ``domains`` for which ``pred`` holds?"""
    names = variables(pred)
    for name in names:
        domains[name]
    clauses = dnf(pred, max_clauses)
    if clauses is None:
        return _UNKNOWN
    unknown = False
    for clause in clauses:
        got = _solve_clause(clause, domains, names)
        if got.status is Status.SAT:
            assert got.witness is not None
            if holds(pred, got.witness) is True:
                return got
            unknown = True  # never expected; refuse to claim what cannot be confirmed
        elif got.status is Status.UNKNOWN:
            unknown = True
    return _UNKNOWN if unknown else _UNSAT


def overlap(p: Pred, q: Pred, domains: dict[str, Domain]) -> Result:
    """``SAT`` with a case where both hold; ``UNSAT`` proves they exclude each other."""
    return check(And((p, q)), domains)


def uncovered(preds: list[Pred], domains: dict[str, Domain]) -> Result:
    """``SAT`` with a case that none holds; ``UNSAT`` proves that together they cover everything."""
    return check(And(tuple(Not(p) for p in preds)), domains)


def entails(p: Pred, q: Pred, domains: dict[str, Domain]) -> Result:
    """``UNSAT`` proves that ``p`` implies ``q``; ``SAT`` is a counterexample."""
    return check(And((p, Not(q))), domains)


# -- one clause -------------------------------------------------------------------------------
_Sets = dict[str, NumberSet | StringSet | BoolSet]


def _solve_clause(clause: Clause, domains: dict[str, Domain], names: set[str]) -> Result:
    opaque = any(isinstance(a, Opaque) for a in clause)
    atoms = [a for a in clause if not isinstance(a, Opaque)]
    sets: _Sets = {}
    for name in sorted(names):
        d = domains[name]
        sets[name] = (
            NumberSet.of(d)
            if d.kind in ("int", "real")
            else BoolSet.of(d)
            if d.kind == "bool"
            else StringSet.of(d)
        )
    diffs: list[Diff] = []
    for atom in atoms:
        if isinstance(atom, Diff):
            diffs.append(atom)
        elif not _restrict(sets, atom):
            return _UNSAT
    if any(s.empty for s in sets.values()):
        return _UNSAT
    chosen = _solve_differences(diffs, sets, domains)
    if chosen is None:
        return _UNSAT
    if chosen is _UNKNOWN_VALUES:
        return _UNKNOWN
    if opaque:
        return _UNKNOWN  # the rest can hold, but the opaque atom may not
    assert isinstance(chosen, dict)
    witness: dict[str, object] = {}
    for name in sorted(names):
        if name in chosen:
            witness[name] = chosen[name]
        else:
            value = sets[name].sample()
            witness[name] = int(value) if domains[name].kind == "int" else value
    return Result(Status.SAT, witness)


def _restrict(sets: _Sets, atom: Atom) -> bool:
    """Apply an atom on one variable; ``False`` when it is known to be false."""
    match atom:
        case Cmp(var=var, op=op, const=const):
            s = sets[var]
            if isinstance(s, NumberSet):
                if isinstance(const, str):
                    return op == "!="  # a string is never equal to a number
                sets[var] = s.restrict(op, const)
            elif isinstance(s, StringSet):
                if not isinstance(const, str):
                    return op == "!="
                if op not in ("==", "!="):
                    raise ValueError("ordering of strings is not part of the fragment")
                sets[var] = s.restrict(op, const)
            else:
                raise ValueError("a boolean variable is compared with Truth")
        case Member(var=var, consts=consts, negated=negated):
            s = sets[var]
            if isinstance(s, NumberSet):
                numbers = tuple(c for c in consts if isinstance(c, Fraction))
                sets[var] = s.restrict_in(numbers, negated)
            elif isinstance(s, StringSet):
                strings = tuple(c for c in consts if isinstance(c, str))
                sets[var] = s.restrict_in(strings, negated)
            else:
                raise ValueError("membership of a boolean variable is a Truth atom")
        case Truth(var=var, negated=negated):
            s = sets[var]
            if not isinstance(s, BoolSet):
                raise ValueError("Truth needs a boolean variable")
            sets[var] = s.restrict(not negated)
        case _:
            raise AssertionError(atom)
    return True


class _Unknown:
    pass


_UNKNOWN_VALUES = _Unknown()


def _solve_differences(
    diffs: list[Diff], sets: _Sets, domains: dict[str, Domain]
) -> dict[str, object] | _Unknown | None:
    """Values of the variables that meet in a difference; ``None`` if there are none (unsat)."""
    real_diffs: list[Diff] = []
    for d in diffs:
        if d.x == d.y:  # x - x op c is a comparison of constants
            if _constant_holds(d.op, d.const):
                continue
            return None
        real_diffs.append(d)
    if not real_diffs:
        return {}
    names = sorted({n for d in real_diffs for n in (d.x, d.y)})
    node = {name: i + 1 for i, name in enumerate(names)}  # node 0 is the zero
    pieces: list[tuple[Interval, ...]] = []
    for name in names:
        s = sets[name]
        if not isinstance(s, NumberSet):
            raise ValueError("a difference needs numeric variables")
        pieces.append(s.pieces)
    if math.prod(len(p) for p in pieces) > MAX_COMBINATIONS:
        return _UNKNOWN_VALUES
    integral = {n: domains[n].kind == "int" for n in names}
    failed = False
    for combo in itertools.product(*pieces):
        edges = _edges(real_diffs, names, node, combo, integral)
        values = solve_differences(len(names) + 1, edges)
        if values is None:
            continue
        out: dict[str, object] = {}
        ok = True
        for name in names:
            v = values[node[name]] - values[0]
            if integral[name]:
                if v.denominator != 1:
                    ok = False  # a mixed system over the reals gave a fraction for an integer
                    break
                out[name] = int(v)
            else:
                out[name] = v
        if ok:
            return out
        failed = True
    return _UNKNOWN_VALUES if failed else None


def _edges(
    diffs: list[Diff],
    names: list[str],
    node: dict[str, int],
    combo: tuple[Interval, ...],
    integral: dict[str, bool],
) -> list[Edge]:
    edges: list[Edge] = []
    for name, piece in zip(names, combo, strict=True):
        n = node[name]
        if piece.hi is not None:
            edges.append((0, n, piece.hi, 0 if piece.hi_closed else -1))
        if piece.lo is not None:
            edges.append((n, 0, -piece.lo, 0 if piece.lo_closed else -1))
    for d in diffs:
        both = integral[d.x] and integral[d.y]
        x, y = node[d.x], node[d.y]
        if d.op in ("<=", "<", "=="):
            edges.append((y, x, *_bound(d.const, d.op == "<", both)))
        if d.op in (">=", ">", "=="):
            edges.append((x, y, *_bound(-d.const, d.op == ">", both)))
    return edges


def _bound(c: Fraction, strict: bool, integral: bool) -> tuple[Fraction, int]:
    """The weight of ``x - y <= c`` (``< c`` when strict); between integers it is tightened."""
    if integral:
        return (Fraction(math.ceil(c) - 1) if strict else Fraction(math.floor(c)), 0)
    return (c, -1 if strict else 0)


def _constant_holds(op: str, c: Fraction) -> bool:
    return {"<": c > 0, "<=": c >= 0, ">": c < 0, ">=": c <= 0, "==": c == 0, "!=": c != 0}[op]
