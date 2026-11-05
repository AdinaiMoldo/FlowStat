"""Tests of the predicate tree, negation normal form, DNF and three-valued evaluation (C6)."""

from __future__ import annotations

from fractions import Fraction as F

import pytest

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
    dnf,
    has_opaque,
    holds,
    nnf,
    show,
    variables,
)

X_LT_5 = Cmp("x", "<", F(5))
X_GT_1 = Cmp("x", ">", F(1))
S_A = Cmp("s", "==", "a")
B = Truth("b")


def test_show_is_compact_and_readable() -> None:
    assert show(X_LT_5) == "x < 5"
    assert show(Cmp("x", ">=", F(5, 2))) == "x >= 5/2"
    assert show(S_A) == "s == 'a'"
    assert show(Diff("x", "y", "<=", F(3))) == "x - y <= 3"
    assert show(Member("s", ("a", "b"))) == "s in ['a', 'b']"
    assert show(Member("s", ("a",), negated=True)) == "s not in ['a']"
    assert show(Truth("b")) == "b"
    assert show(Truth("b", negated=True)) == "not b"
    assert show(Opaque("len(x) > 0")) == "?(len(x) > 0)"
    assert show(And((X_LT_5, Or((S_A, B))))) == "(x < 5 and (s == 'a' or b))"
    assert show(Not(X_LT_5)) == "not x < 5"
    assert show(TRUE) == "true"
    assert show(FALSE) == "false"


def test_variables() -> None:
    p = And((X_LT_5, Or((Diff("y", "z", "<", F(1)), Not(Truth("b")), Opaque("f(q)")))))
    assert variables(p) == {"x", "y", "z", "b"}


def test_has_opaque() -> None:
    assert not has_opaque(And((X_LT_5, Or((S_A, Not(B))))))
    assert has_opaque(And((X_LT_5, Or((S_A, Not(Opaque("q")))))))
    assert has_opaque(Opaque("q"))


# -- negation normal form ---------------------------------------------------------------------
@pytest.mark.parametrize(
    ("op", "flipped"),
    [("<", ">="), ("<=", ">"), (">", "<="), (">=", "<"), ("==", "!="), ("!=", "==")],
)
def test_negation_flips_comparisons(op: str, flipped: str) -> None:
    assert nnf(Not(Cmp("x", op, F(1)))) == Cmp("x", flipped, F(1))
    assert nnf(Not(Diff("x", "y", op, F(1)))) == nnf(Diff("x", "y", flipped, F(1)))


def test_negation_of_the_other_atoms() -> None:
    assert nnf(Not(Member("s", ("a",)))) == Member("s", ("a",), negated=True)
    assert nnf(Not(Member("s", ("a",), negated=True))) == Member("s", ("a",))
    assert nnf(Not(Truth("b"))) == Truth("b", negated=True)
    assert nnf(Not(Not(Truth("b")))) == Truth("b")
    assert nnf(Not(Opaque("q"))) == Opaque("q")
    assert nnf(Not(TRUE)) == FALSE
    assert nnf(Not(FALSE)) == TRUE


def test_de_morgan() -> None:
    got = nnf(Not(And((X_LT_5, Or((S_A, B))))))
    assert got == Or((Cmp("x", ">=", F(5)), And((Cmp("s", "!=", "a"), Truth("b", negated=True)))))


def test_a_difference_that_must_differ_becomes_a_choice() -> None:
    got = nnf(Diff("x", "y", "!=", F(2)))
    assert got == Or((Diff("x", "y", "<", F(2)), Diff("x", "y", ">", F(2))))


# -- DNF --------------------------------------------------------------------------------------
def test_dnf_of_an_atom_and_of_constants() -> None:
    assert dnf(X_LT_5, 64) == [(X_LT_5,)]
    assert dnf(TRUE, 64) == [()]
    assert dnf(FALSE, 64) == []


def test_dnf_distributes() -> None:
    p = And((Or((S_A, B)), Or((X_LT_5, X_GT_1))))
    assert dnf(p, 64) == [(S_A, X_LT_5), (S_A, X_GT_1), (B, X_LT_5), (B, X_GT_1)]


def test_dnf_drops_true_and_false() -> None:
    assert dnf(And((TRUE, S_A)), 64) == [(S_A,)]
    assert dnf(And((FALSE, S_A)), 64) == []
    assert dnf(Or((FALSE, S_A)), 64) == [(S_A,)]
    assert dnf(Or((TRUE, S_A)), 64) == [(), (S_A,)]


def test_dnf_removes_a_repeated_atom_in_a_clause() -> None:
    assert dnf(And((S_A, S_A, X_LT_5)), 64) == [(S_A, X_LT_5)]


def test_the_clause_cap() -> None:
    parts = tuple(Or((Cmp(f"v{i}", "<", F(1)), Cmp(f"v{i}", ">", F(2)))) for i in range(7))
    p = And(parts)  # 2^7 = 128 clauses
    assert dnf(p, 64) is None
    got = dnf(p, 128)
    assert got is not None and len(got) == 128
    assert dnf(And(parts[:6]), 64) is not None  # exactly 64


def test_the_cap_is_checked_while_expanding() -> None:
    """A huge product is refused without being built: the cap applies to every intermediate."""
    parts = tuple(Or((Cmp(f"v{i}", "<", F(1)), Cmp(f"v{i}", ">", F(2)))) for i in range(60))
    assert dnf(And(parts), 64) is None


# -- three-valued evaluation ------------------------------------------------------------------
ENV: dict[str, object] = {"x": 3, "y": F(1, 2), "s": "a", "b": True}


@pytest.mark.parametrize(
    ("pred", "expected"),
    [
        (X_LT_5, True),
        (Cmp("x", "==", F(3)), True),
        (Cmp("x", "!=", F(3)), False),
        (Cmp("y", ">", F(0)), True),
        (S_A, True),
        (Cmp("s", "!=", "a"), False),
        (Truth("b"), True),
        (Truth("b", negated=True), False),
        (Member("s", ("a", "z")), True),
        (Member("s", ("z",)), False),
        (Member("s", ("z",), negated=True), True),
        (Diff("x", "y", ">", F(2)), True),
        (Diff("x", "y", "<=", F(2)), False),
        (Diff("x", "y", "==", F(5, 2)), True),
        (Not(X_LT_5), False),
        (TRUE, True),
        (FALSE, False),
    ],
)
def test_holds(pred: Pred, expected: bool) -> None:
    assert holds(pred, ENV) is expected


def test_holds_with_a_float_and_a_bool_value() -> None:
    assert holds(Cmp("y", "==", F(1, 2)), {"y": 0.5}) is True
    assert holds(Cmp("x", "==", F(1)), {"x": True}) is False  # true is not 1 (dsl-spec 3.1)


def test_kleene_logic_for_opaque_atoms() -> None:
    q = Opaque("q")
    assert holds(q, ENV) is None
    assert holds(Not(q), ENV) is None
    assert holds(And((FALSE, q)), ENV) is False
    assert holds(And((q, FALSE)), ENV) is False
    assert holds(And((TRUE, q)), ENV) is None
    assert holds(Or((TRUE, q)), ENV) is True
    assert holds(Or((q, TRUE)), ENV) is True
    assert holds(Or((FALSE, q)), ENV) is None
