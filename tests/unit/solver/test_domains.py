"""Tests of the per-variable domains (C6). Written before the code."""

from __future__ import annotations

from fractions import Fraction as F

import pytest

from flowstat.solver.domains import Domain, Interval, NumberSet, StringSet


def ivs(*pieces: tuple[object, bool, object, bool]) -> tuple[Interval, ...]:
    return tuple(
        Interval(None if lo is None else F(lo), lc, None if hi is None else F(hi), hc)  # type: ignore[call-overload]
        for lo, lc, hi, hc in pieces
    )


# -- numbers over the reals -------------------------------------------------------------------
def test_a_new_real_domain_is_the_whole_line() -> None:
    s = NumberSet.of(Domain.real())
    assert s.pieces == ivs((None, False, None, False))
    assert not s.empty


@pytest.mark.parametrize(
    ("op", "c", "expected"),
    [
        ("<", 3, [(None, False, 3, False)]),
        ("<=", 3, [(None, False, 3, True)]),
        (">", 3, [(3, False, None, False)]),
        (">=", 3, [(3, True, None, False)]),
        ("==", 3, [(3, True, 3, True)]),
        ("!=", 3, [(None, False, 3, False), (3, False, None, False)]),
    ],
)
def test_one_constraint_on_a_real(op: str, c: int, expected: list[tuple[object, ...]]) -> None:
    s = NumberSet.of(Domain.real()).restrict(op, F(c))
    assert s.pieces == ivs(*expected)  # type: ignore[arg-type]


def test_constraints_intersect() -> None:
    s = NumberSet.of(Domain.real()).restrict(">", F(1)).restrict("<=", F(4)).restrict("!=", F(2))
    assert s.pieces == ivs((1, False, 2, False), (2, False, 4, True))


def test_an_empty_interval_disappears() -> None:
    s = NumberSet.of(Domain.real()).restrict(">", F(3)).restrict("<", F(3))
    assert s.empty
    assert NumberSet.of(Domain.real()).restrict(">", F(3)).restrict("<=", F(3)).empty
    assert not NumberSet.of(Domain.real()).restrict(">=", F(3)).restrict("<=", F(3)).empty


def test_removing_a_point_that_is_not_there_changes_nothing() -> None:
    s = NumberSet.of(Domain.real(lo=0, hi=5)).restrict("!=", F(9))
    assert s.pieces == ivs((0, True, 5, True))


def test_bounds_of_the_declaration_apply() -> None:
    s = NumberSet.of(Domain.real(lo=0, hi=5))
    assert s.pieces == ivs((0, True, 5, True))


def test_allowed_values_of_a_number_become_points() -> None:
    s = NumberSet.of(Domain.integer(values=(1, 3, 5)))
    assert s.pieces == ivs((1, True, 1, True), (3, True, 3, True), (5, True, 5, True))
    assert s.restrict(">", F(2)).pieces == ivs((3, True, 3, True), (5, True, 5, True))


# -- numbers over the integers ----------------------------------------------------------------
@pytest.mark.parametrize(
    ("op", "c", "expected"),
    [
        ("<", F(6), [(None, False, 5, True)]),
        ("<", F(11, 2), [(None, False, 5, True)]),
        ("<=", F(11, 2), [(None, False, 5, True)]),
        (">", F(6), [(7, True, None, False)]),
        (">=", F(11, 2), [(6, True, None, False)]),
        (">", F(11, 2), [(6, True, None, False)]),
        ("==", F(6), [(6, True, 6, True)]),
        ("!=", F(6), [(None, False, 5, True), (7, True, None, False)]),
    ],
)
def test_integers_are_tightened(op: str, c: F, expected: list[tuple[object, ...]]) -> None:
    assert NumberSet.of(Domain.integer()).restrict(op, c).pieces == ivs(*expected)  # type: ignore[arg-type]


def test_an_integer_is_never_equal_to_a_fraction() -> None:
    s = NumberSet.of(Domain.integer())
    assert s.restrict("==", F(5, 2)).empty
    assert s.restrict("!=", F(5, 2)).pieces == s.pieces


def test_no_integer_between_two_neighbours() -> None:
    assert NumberSet.of(Domain.integer()).restrict(">", F(3)).restrict("<", F(4)).empty
    assert not NumberSet.of(Domain.real()).restrict(">", F(3)).restrict("<", F(4)).empty


def test_integer_bounds_of_the_declaration_are_tightened() -> None:
    s = NumberSet.of(Domain.integer(lo=F(1, 2), hi=F(7, 2)))
    assert s.pieces == ivs((1, True, 3, True))


# -- samples ----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("domain", "ops", "expected"),
    [
        (Domain.real(), [], 0),
        (Domain.integer(), [(">", 4)], 5),
        (Domain.integer(), [("<", -4)], -5),
        (Domain.real(), [(">", 4)], 5),
        (Domain.real(), [(">", 1), ("<", 2)], F(3, 2)),
        (Domain.real(), [(">=", 1), ("<=", 1)], 1),
        (Domain.integer(lo=2, hi=9), [], 2),
        (Domain.real(), [("<=", -2)], -2),
    ],
)
def test_sample_is_a_member(domain: Domain, ops: list[tuple[str, int]], expected: object) -> None:
    s = NumberSet.of(domain)
    for op, c in ops:
        s = s.restrict(op, F(c))
    got = s.sample()
    assert got == expected
    assert s.contains(got)


def test_contains_respects_open_and_closed_ends() -> None:
    s = NumberSet.of(Domain.real()).restrict(">", F(1)).restrict("<=", F(2))
    assert not s.contains(F(1))
    assert s.contains(F(2))
    assert not s.contains(F(3))


# -- strings ----------------------------------------------------------------------------------
def test_an_enumeration_is_a_finite_set() -> None:
    s = StringSet.of(Domain.enum("approved", "rejected"))
    assert s.restrict("!=", "approved").values() == {"rejected"}
    assert s.restrict("==", "approved").values() == {"approved"}
    assert s.restrict("==", "escalated").empty
    assert s.restrict("!=", "approved").restrict("!=", "rejected").empty


def test_a_free_string_is_cofinite() -> None:
    s = StringSet.of(Domain.string())
    assert not s.restrict("!=", "a").empty
    assert s.restrict("==", "a").values() == {"a"}
    assert s.restrict("==", "a").restrict("!=", "a").empty


def test_a_sample_of_a_free_string_avoids_the_excluded_ones() -> None:
    s = StringSet.of(Domain.string()).restrict("!=", "x").restrict("!=", "x1")
    got = s.sample()
    assert got not in {"x", "x1"}
    assert s.contains(got)


def test_a_sample_of_an_enumeration_is_the_first_value_in_order() -> None:
    s = StringSet.of(Domain.enum("rejected", "approved"))
    assert s.sample() == "approved"
    assert s.restrict("!=", "approved").sample() == "rejected"


def test_domain_helpers_reject_nonsense() -> None:
    with pytest.raises(ValueError):
        Domain.enum()
    with pytest.raises(ValueError):
        Domain("blob")
