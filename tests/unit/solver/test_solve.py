"""Tests of the solver (C6): golden cases, the three answers, and a comparison with brute force."""

from __future__ import annotations

import itertools
import random
from fractions import Fraction as F

import pytest

from flowstat.solver.domains import Domain
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
    holds,
)
from flowstat.solver.solve import Status, check, entails, overlap, uncovered

SAT, UNSAT, UNKNOWN = Status.SAT, Status.UNSAT, Status.UNKNOWN

DECISION = {"d": Domain.enum("approved", "rejected")}
DECISION3 = {"d": Domain.enum("approved", "rejected", "escalated")}
INTS = {"x": Domain.integer(), "y": Domain.integer(), "z": Domain.integer()}
REALS = {"x": Domain.real(), "y": Domain.real(), "z": Domain.real()}


def lt(v: str, c: int | F) -> Pred:
    return Cmp(v, "<", F(c))


def gt(v: str, c: int | F) -> Pred:
    return Cmp(v, ">", F(c))


def eq(v: str, c: int | str) -> Pred:
    return Cmp(v, "==", c if isinstance(c, str) else F(c))


def sat_with_witness(pred: Pred, domains: dict[str, Domain]) -> dict[str, object]:
    result = check(pred, domains)
    assert result.status is SAT, result
    assert result.witness is not None
    assert holds(pred, result.witness) is True, result.witness
    return result.witness


# -- the example of US-20 ---------------------------------------------------------------------
def test_publish_and_return_to_regions_neither_overlap_nor_leave_a_gap() -> None:
    publish, back = eq("d", "approved"), eq("d", "rejected")
    assert overlap(publish, back, DECISION).status is UNSAT
    assert uncovered([publish, back], DECISION).status is UNSAT


def test_an_extra_decision_is_reported_as_uncovered_with_its_name() -> None:
    publish, back = eq("d", "approved"), eq("d", "rejected")
    result = uncovered([publish, back], DECISION3)
    assert result.status is SAT
    assert result.witness == {"d": "escalated"}


def test_overlapping_guards_come_with_a_value() -> None:
    result = overlap(gt("x", 3), lt("x", 10), INTS)
    assert result.status is SAT and result.witness is not None
    assert 3 < result.witness["x"] < 10  # type: ignore[operator]


# -- numbers ----------------------------------------------------------------------------------
def test_integers_have_no_value_between_neighbours() -> None:
    p = And((gt("x", 3), lt("x", 4)))
    assert check(p, INTS).status is UNSAT
    w = sat_with_witness(p, REALS)
    assert 3 < w["x"] < 4  # type: ignore[operator]


def test_strict_and_closed_ends() -> None:
    assert check(And((gt("x", 3), Cmp("x", "<=", F(3)))), REALS).status is UNSAT
    assert sat_with_witness(And((Cmp("x", ">=", F(3)), Cmp("x", "<=", F(3)))), REALS) == {"x": 3}


def test_complementary_guards_cover_the_integers_but_not_the_boundary_case() -> None:
    assert uncovered([lt("x", 5), Cmp("x", ">=", F(5))], INTS).status is UNSAT
    gap = uncovered([lt("x", 5), gt("x", 5)], INTS)
    assert gap.status is SAT and gap.witness == {"x": 5}


def test_exact_decimals() -> None:
    """0.1 + 0.2 style traps: the constants are exact rationals."""
    p = And((Cmp("x", ">", F(1, 10)), Cmp("x", "<", F(3, 10)), Cmp("x", "!=", F(1, 5))))
    w = sat_with_witness(p, REALS)
    assert F(1, 10) < w["x"] < F(3, 10) and w["x"] != F(1, 5)  # type: ignore[operator]


def test_declared_bounds_make_a_guard_dead() -> None:
    domains = {"x": Domain.integer(lo=0, hi=100)}
    assert check(gt("x", 100), domains).status is UNSAT
    assert check(lt("x", 0), domains).status is UNSAT
    assert check(eq("x", 100), domains).status is SAT


def test_allowed_values_of_a_number() -> None:
    domains = {"x": Domain.integer(values=(1, 2, 3))}
    assert check(eq("x", 4), domains).status is UNSAT
    assert sat_with_witness(gt("x", 2), domains) == {"x": 3}


# -- differences ------------------------------------------------------------------------------
def test_difference_constraints() -> None:
    assert check(And((Diff("x", "y", ">", F(2)), Diff("y", "x", ">", F(2)))), INTS).status is UNSAT
    w = sat_with_witness(Diff("x", "y", ">", F(2)), INTS)
    assert w["x"] - w["y"] > 2  # type: ignore[operator]


def test_a_difference_between_integers_is_tightened() -> None:
    p = And((Diff("x", "y", "<", F(2)), Diff("x", "y", ">", F(1))))
    assert check(p, INTS).status is UNSAT
    w = sat_with_witness(p, REALS)
    assert 1 < w["x"] - w["y"] < 2  # type: ignore[operator]


def test_a_chain_through_three_variables() -> None:
    p = And((Diff("x", "y", "<", F(0)), Diff("y", "z", "<", F(0)), Diff("z", "x", "<", F(0))))
    assert check(p, REALS).status is UNSAT
    q = And((Diff("x", "y", "<", F(0)), Diff("y", "z", "<", F(0))))
    w = sat_with_witness(q, REALS)
    assert w["x"] < w["y"] < w["z"]  # type: ignore[operator]


def test_a_difference_meets_the_bounds() -> None:
    domains = {"x": Domain.integer(lo=0, hi=3), "y": Domain.integer(lo=0, hi=3)}
    assert check(Diff("x", "y", ">", F(3)), domains).status is UNSAT
    assert check(Diff("x", "y", ">=", F(3)), domains).status is SAT
    w = sat_with_witness(Diff("x", "y", ">=", F(3)), domains)
    assert w == {"x": 3, "y": 0}


def test_a_difference_with_a_hole_in_the_domain() -> None:
    p = And(
        (
            Cmp("x", "!=", F(1)),
            Cmp("y", "==", F(0)),
            Diff("x", "y", "<=", F(1)),
            Cmp("x", ">", F(0)),
        )
    )
    assert check(p, INTS).status is UNSAT  # x in {1} only, and x != 1
    w = sat_with_witness(p, REALS)
    assert 0 < w["x"] <= 1 and w["x"] != 1  # type: ignore[operator]


def test_a_difference_that_must_differ() -> None:
    p = And(
        (
            Diff("x", "y", "!=", F(0)),
            Cmp("x", ">=", F(0)),
            Cmp("x", "<=", F(0)),
            Cmp("y", ">=", F(0)),
            Cmp("y", "<=", F(0)),
        )
    )
    assert check(p, INTS).status is UNSAT
    w = sat_with_witness(Diff("x", "y", "!=", F(0)), INTS)
    assert w["x"] != w["y"]


def test_equal_difference() -> None:
    w = sat_with_witness(And((Diff("x", "y", "==", F(5)), Cmp("y", "==", F(2)))), INTS)
    assert w == {"x": 7, "y": 2}


def test_a_mixed_integer_and_real_difference_is_never_wrong() -> None:
    domains = {"i": Domain.integer(), "r": Domain.real()}
    p = And((Diff("i", "r", "<", F(1, 2)), Diff("i", "r", ">", F(1, 4))))
    result = check(p, domains)
    assert result.status in (SAT, UNKNOWN)
    if result.status is SAT:
        assert result.witness is not None and holds(p, result.witness) is True
    assert (
        check(And((Diff("i", "r", "<", F(0)), Diff("i", "r", ">", F(0)))), domains).status is UNSAT
    )


# -- strings, enumerations, booleans ----------------------------------------------------------
def test_membership() -> None:
    assert (
        check(And((Member("d", ("approved", "rejected")), eq("d", "escalated"))), DECISION3).status
        is UNSAT
    )
    assert sat_with_witness(Member("d", ("approved", "escalated")), DECISION3) == {"d": "approved"}
    assert sat_with_witness(Member("d", ("approved",), negated=True), DECISION3) == {
        "d": "escalated"
    }


def test_a_free_string() -> None:
    domains = {"s": Domain.string()}
    assert check(And((Cmp("s", "!=", "a"), Cmp("s", "!=", "b"))), domains).status is SAT
    assert check(And((eq("s", "a"), eq("s", "b"))), domains).status is UNSAT
    w = sat_with_witness(Member("s", ("a", "b"), negated=True), domains)
    assert w["s"] not in ("a", "b")


def test_booleans() -> None:
    domains = {"b": Domain.boolean(), "c": Domain.boolean()}
    assert check(And((Truth("b"), Truth("b", negated=True))), domains).status is UNSAT
    assert sat_with_witness(And((Truth("b"), Truth("c", negated=True))), domains) == {
        "b": True,
        "c": False,
    }
    assert uncovered([Truth("b"), Truth("b", negated=True)], domains).status is UNSAT


def test_unconstrained_variables_get_a_default_in_their_domain() -> None:
    domains = {"x": Domain.integer(lo=5, hi=9), "s": Domain.enum("p", "q"), "b": Domain.boolean()}
    w = sat_with_witness(And((Truth("b"), Cmp("s", "!=", "p"))), domains)
    assert w == {"b": True, "s": "q"}  # only the variables of the predicate


# -- entailment -------------------------------------------------------------------------------
def test_entails() -> None:
    assert entails(gt("x", 5), gt("x", 3), INTS).status is UNSAT  # the proof: no counterexample
    counter = entails(gt("x", 3), gt("x", 5), INTS)
    assert counter.status is SAT and counter.witness == {"x": 4}


# -- constants and the three answers ----------------------------------------------------------
def test_true_and_false() -> None:
    assert check(TRUE, {}).status is SAT and check(TRUE, {}).witness == {}
    assert check(FALSE, {}).status is UNSAT


def test_a_clause_with_an_opaque_atom_is_unsat_only_if_the_rest_is() -> None:
    q = Opaque("len(${a.rows}) > 0")
    assert check(And((gt("x", 5), lt("x", 3), q)), INTS).status is UNSAT
    assert check(And((gt("x", 5), q)), INTS).status is UNKNOWN  # never SAT: q may be false


def test_a_clean_clause_is_enough_for_sat() -> None:
    q = Opaque("q")
    result = check(Or((q, gt("x", 5))), INTS)
    assert result.status is SAT and result.witness is not None and result.witness["x"] > 5  # type: ignore[operator]


def test_opaque_alone_and_negated() -> None:
    q = Opaque("q")
    assert check(q, {}).status is UNKNOWN
    assert check(Not(q), {}).status is UNKNOWN
    assert check(And((q, Not(q))), {}).status is UNKNOWN  # we do not reason about q
    assert check(Or((q, FALSE)), {}).status is UNKNOWN


def test_overlap_with_an_opaque_guard_claims_nothing() -> None:
    assert overlap(Opaque("q"), gt("x", 1), INTS).status is UNKNOWN
    assert uncovered([Opaque("q"), gt("x", 1)], INTS).status is UNKNOWN


def test_too_many_clauses_is_unknown_not_unsat() -> None:
    parts = tuple(Or((lt(f"v{i}", 1), gt(f"v{i}", 2))) for i in range(7))
    domains = {f"v{i}": Domain.integer() for i in range(7)}
    p = And(parts)
    assert check(p, domains).status is UNKNOWN
    assert check(p, domains, max_clauses=128).status is SAT


def test_a_variable_without_a_domain_is_an_error_of_the_caller() -> None:
    with pytest.raises(KeyError):
        check(gt("nope", 1), INTS)


def test_results_are_deterministic() -> None:
    p = And((Diff("x", "y", "<", F(3)), Diff("y", "z", "<=", F(1)), Cmp("x", "!=", F(0))))
    assert check(p, INTS) == check(p, INTS)


# -- brute force ------------------------------------------------------------------------------
BOUNDED_INTS = {
    "i": Domain.integer(lo=-3, hi=3),
    "j": Domain.integer(lo=-3, hi=3),
    "s": Domain.enum("a", "b", "c"),
    "b": Domain.boolean(),
}
OPS = ["<", "<=", ">", ">=", "==", "!="]


def _atom(rng: random.Random, domains: dict[str, Domain]) -> Pred:
    names = list(domains)
    v = rng.choice(names)
    kind = domains[v].kind
    if kind == "bool":
        return Truth(v, rng.random() < 0.5)
    if kind == "str":
        if rng.random() < 0.5:
            return Cmp(v, rng.choice(["==", "!="]), rng.choice(["a", "b", "c", "z"]))
        picks = tuple(rng.sample(["a", "b", "c", "z"], rng.randint(1, 3)))
        return Member(v, picks, rng.random() < 0.3)
    numeric = [n for n in names if domains[n].kind in ("int", "real")]
    if rng.random() < 0.35 and len(numeric) > 1:
        a, b = rng.sample(numeric, 2)
        return Diff(a, b, rng.choice(OPS), F(rng.randint(-4, 4), rng.choice([1, 2])))
    if rng.random() < 0.15:
        return Member(
            v, tuple(F(rng.randint(-3, 3)) for _ in range(rng.randint(1, 3))), rng.random() < 0.4
        )
    return Cmp(v, rng.choice(OPS), F(rng.randint(-8, 8), rng.choice([1, 2])))


def _tree(rng: random.Random, domains: dict[str, Domain], depth: int) -> Pred:
    if depth == 0 or rng.random() < 0.3:
        return _atom(rng, domains)
    roll = rng.random()
    if roll < 0.15:
        return Not(_tree(rng, domains, depth - 1))
    items = tuple(_tree(rng, domains, depth - 1) for _ in range(rng.randint(2, 3)))
    return And(items) if roll < 0.6 else Or(items)


def _assignments(domains: dict[str, Domain], step: F) -> list[dict[str, object]]:
    axes: list[list[object]] = []
    for d in domains.values():
        if d.kind == "bool":
            axes.append([False, True])
        elif d.kind == "str":
            axes.append(list(d.values or ()))
        else:
            assert d.lo is not None and d.hi is not None
            n = int((d.hi - d.lo) / step)
            axes.append([d.lo + step * k for k in range(n + 1)])
    return [dict(zip(domains, combo, strict=True)) for combo in itertools.product(*axes)]


def test_exact_on_bounded_integers_enumerations_and_booleans() -> None:
    rng = random.Random(20261105)
    grid = _assignments(BOUNDED_INTS, F(1))
    answers = {SAT: 0, UNSAT: 0}
    for _ in range(500):
        p = _tree(rng, BOUNDED_INTS, 3)
        brute = any(holds(p, a) for a in grid)
        result = check(p, BOUNDED_INTS, max_clauses=5000)  # the cap is tested elsewhere
        assert result.status is (SAT if brute else UNSAT), p
        answers[result.status] += 1
        if result.witness is not None:
            full = {**{"i": 0, "j": 0, "s": "a", "b": False}, **result.witness}
            assert holds(p, full) is True, (p, result.witness)
    assert answers[SAT] > 100 and answers[UNSAT] > 60  # both kinds are well represented


REAL_DOMAINS = {
    "r": Domain.real(lo=-3, hi=3),
    "q": Domain.real(lo=-3, hi=3),
    "i": Domain.integer(lo=-2, hi=2),
    "s": Domain.enum("a", "b"),
}


def test_reals_never_give_a_wrong_answer() -> None:
    """Grid search on halves: a hit forbids UNSAT, UNSAT forbids a hit, witnesses are verified."""
    rng = random.Random(99)
    grid = _assignments(REAL_DOMAINS, F(1, 2))
    counts = {SAT: 0, UNSAT: 0, UNKNOWN: 0}
    for _ in range(250):
        p = _tree(rng, REAL_DOMAINS, 3)
        brute = any(holds(p, a) for a in grid)
        result = check(p, REAL_DOMAINS, max_clauses=5000)
        counts[result.status] += 1
        if brute:
            assert result.status is not UNSAT, p
        if result.status is UNSAT:
            assert not brute, p
        if result.status is SAT:
            assert result.witness is not None
            full = {"r": F(0), "q": F(0), "i": 0, "s": "a", **result.witness}
            assert holds(p, full) is True, (p, result.witness)
    assert counts[UNSAT] > 30 and counts[SAT] > 100
    assert counts[UNKNOWN] <= 10  # mixed integer and real differences only


# -- corners ----------------------------------------------------------------------------------
def test_a_difference_of_a_variable_with_itself_is_a_constant() -> None:
    assert check(Diff("x", "x", "<", F(1)), INTS).status is SAT
    assert check(Diff("x", "x", ">", F(0)), INTS).status is UNSAT
    assert check(Diff("x", "x", "==", F(0)), INTS).status is SAT


def test_too_many_pieces_in_a_difference_is_unknown() -> None:
    """Holes in two variables that meet in a difference multiply; past the limit it gives up."""
    holes = tuple(Cmp(v, "!=", F(k)) for v in ("x", "y") for k in range(0, 40, 2))
    p = And((*holes, Diff("x", "y", "<", F(1))))
    result = check(p, INTS)
    assert result.status is UNKNOWN


def test_a_type_mismatch_between_a_variable_and_a_constant() -> None:
    assert check(Cmp("x", "==", "a"), INTS).status is UNSAT
    assert check(Cmp("x", "!=", "a"), INTS).status is SAT
    assert check(Cmp("d", "==", F(1)), DECISION).status is UNSAT
    assert check(Cmp("d", "!=", F(1)), DECISION).status is SAT


def test_misuse_is_reported() -> None:
    domains = {**INTS, **DECISION, "b": Domain.boolean()}
    with pytest.raises(ValueError):
        check(Cmp("d", "<", "a"), domains)
    with pytest.raises(ValueError):
        check(Cmp("b", "==", F(1)), domains)
    with pytest.raises(ValueError):
        check(Member("b", (F(1),)), domains)
    with pytest.raises(ValueError):
        check(Truth("x"), domains)
    with pytest.raises(ValueError):
        check(Diff("x", "d", "<", F(1)), domains)


def test_membership_with_the_wrong_kind_of_constant_is_ignored() -> None:
    assert check(Member("x", ("a", F(2))), INTS).witness == {"x": 2}
    assert check(Member("d", (F(1), "approved")), DECISION).witness == {"d": "approved"}
    assert check(Member("x", ("a",)), INTS).status is UNSAT
