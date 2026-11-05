"""Tests of the expression parser (C1, level 1). Written before the parser."""

from __future__ import annotations

import operator
import random
from collections.abc import Callable
from typing import Any

import pytest

from flowstat.dsl.ast import (
    Binary,
    Compare,
    ErrorNode,
    Expr,
    Literal,
    Unary,
    dump,
    to_source,
)
from flowstat.dsl.parser import parse_expression
from flowstat.model.diagnostics import Diagnostic


def parse_ok(text: str) -> Expr:
    expr, diags = parse_expression(text)
    assert diags == [], [d.message for d in diags]
    return expr


def codes(diags: list[Diagnostic]) -> list[str]:
    return [d.code for d in diags]


def walk(expr: Expr) -> list[Expr]:
    """All nodes of a tree."""
    out: list[Expr] = [expr]
    for field in ("operand", "left", "right", "func", "obj", "index"):
        child = getattr(expr, field, None)
        if child is not None:
            out.extend(walk(child))
    for item in getattr(expr, "items", ()):
        out.extend(walk(item))
    for arg in getattr(expr, "args", ()):
        out.extend(walk(arg))
    return out


# -- atoms -------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("42", "42"),
        ("3.5", "3.5"),
        ("'a'", "'a'"),
        ("true", "true"),
        ("false", "false"),
        ("null", "null"),
        ("region", "region"),
        ("${approve.decision}", "${approve.decision}"),
        ("${load[region].rows}", "${load[region].rows}"),
        ("${check[*].rows}", "${check[*].rows}"),
        ("${rows[0]}", "${rows[0]}"),
        ("(1)", "1"),
    ],
)
def test_atoms(text: str, expected: str) -> None:
    assert dump(parse_ok(text)) == expected


def test_reference_path_parts() -> None:
    expr = parse_ok("${load[region].rows[*]}")
    assert [(p.kind, p.value) for p in expr.path] == [  # type: ignore[union-attr]
        ("field", "load"), ("var", "region"), ("field", "rows"), ("star", "*"),
    ]  # fmt: skip


# -- precedence and associativity ------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1 + 2 * 3", "(+ 1 (* 2 3))"),
        ("1 * 2 + 3", "(+ (* 1 2) 3)"),
        ("1 - 2 - 3", "(- (- 1 2) 3)"),
        ("8 / 4 / 2", "(/ (/ 8 4) 2)"),
        ("1 + 2 % 3", "(+ 1 (% 2 3))"),
        ("a or b and c", "(or a (and b c))"),
        ("a and b or c", "(or (and a b) c)"),
        ("a or b or c", "(or (or a b) c)"),
        ("not a and b", "(and (not a) b)"),
        ("not a == b", "(not (== a b))"),
        ("a == b and c != d", "(and (== a b) (!= c d))"),
        ("a + 1 < b * 2", "(< (+ a 1) (* b 2))"),
        ("(1 + 2) * 3", "(* (+ 1 2) 3)"),
        ("-a + b", "(+ (- a) b)"),
        ("- - 1", "(- (- 1))"),
        ("not not a", "(not (not a))"),
        ("-a.b", "(- (. a b))"),
        ("x in y", "(in x y)"),
    ],
)
def test_precedence_and_associativity(text: str, expected: str) -> None:
    assert dump(parse_ok(text)) == expected


# -- postfix -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("len(${consolidate.rows})", "(call len ${consolidate.rows})"),
        ("max(a, b + 1, 3)", "(call max a (+ b 1) 3)"),
        ("f()", "(call f)"),
        ("a.b.c", "(. (. a b) c)"),
        ("a[0][1]", "(idx (idx a 0) 1)"),
        ("f(x).y[2]", "(idx (. (call f x) y) 2)"),
        ("${a} == len(${b})", "(== ${a} (call len ${b}))"),
    ],
)
def test_postfix(text: str, expected: str) -> None:
    assert dump(parse_ok(text)) == expected


def test_main_example_of_the_proposal() -> None:
    tree = parse_ok("${publish.written} == len(${consolidate.rows})")
    assert isinstance(tree, Compare)


# -- positions ---------------------------------------------------------------------------
def test_nodes_carry_the_position_of_their_first_token() -> None:
    expr = parse_ok("1 +\n  23")
    assert isinstance(expr, Binary)
    assert (expr.line, expr.column) == (1, 1)
    assert isinstance(expr.right, Literal)
    assert (expr.right.line, expr.right.column) == (2, 3)


def test_base_position_is_applied() -> None:
    expr, _ = parse_expression("a + b", line=7, column=11)
    assert (expr.line, expr.column) == (7, 11)


# -- errors -------------------------------------------------------------------------------
def test_empty_expression() -> None:
    expr, diags = parse_expression("   ")
    assert codes(diags) == ["E-EXPR-001"]
    assert isinstance(expr, ErrorNode)


def test_missing_right_operand() -> None:
    expr, diags = parse_expression("a +")
    assert codes(diags) == ["E-EXPR-001"]
    assert (diags[0].line, diags[0].column) == (1, 4)
    assert dump(expr) == "(+ a (error))"


def test_unclosed_parenthesis() -> None:
    _, diags = parse_expression("(1 + 2")
    assert codes(diags) == ["E-EXPR-001"]
    assert "')'" in diags[0].message


def test_trailing_tokens_after_a_complete_expression() -> None:
    expr, diags = parse_expression("1 2")
    assert codes(diags) == ["E-EXPR-001"]
    assert (diags[0].line, diags[0].column) == (1, 3)
    assert dump(expr) == "1"


def test_all_syntax_errors_are_reported_in_one_run() -> None:
    expr, diags = parse_expression("(1 + ) and (2 * ) and 3")
    assert codes(diags) == ["E-EXPR-001", "E-EXPR-001"]
    assert [d.column for d in diags] == [6, 17]
    assert dump(expr) == "(and (and (+ 1 (error)) (* 2 (error))) 3)"


def test_recovery_inside_call_arguments() -> None:
    expr, diags = parse_expression("f(1 +, 2, )")
    assert len(diags) == 2  # the missing operand and the empty last argument
    assert dump(expr).startswith("(call f (+ 1 (error)) 2")


def test_chained_comparison_is_reported_once_and_parsing_goes_on() -> None:
    expr, diags = parse_expression("a < b < c and d")
    assert codes(diags) == ["E-EXPR-003"]
    assert (diags[0].line, diags[0].column) == (1, 7)
    assert isinstance(expr, Binary) and expr.op == "and"


def test_lexical_errors_are_forwarded_and_not_doubled() -> None:
    _, diags = parse_expression("a @ b")
    assert codes(diags)[0] == "E-EXPR-005"
    assert codes(diags).count("E-EXPR-005") == 1


def test_unclosed_reference_is_reported_once() -> None:
    _, diags = parse_expression("len(${a.b")
    assert codes(diags).count("E-EXPR-002") == 1
    assert all(c in ("E-EXPR-002", "E-EXPR-001") for c in codes(diags))


def test_bad_reference_path() -> None:
    expr, diags = parse_expression("${a..b}")
    assert "E-EXPR-001" in codes(diags)
    assert isinstance(expr, ErrorNode) or any(isinstance(n, ErrorNode) for n in walk(expr))


def test_deep_nesting_is_an_error_not_a_crash() -> None:
    text = "(" * 5000 + "1" + ")" * 5000
    _, diags = parse_expression(text)
    assert "E-EXPR-006" in codes(diags)
    deep_not = "not " * 5000 + "a"
    _, diags = parse_expression(deep_not)
    assert "E-EXPR-006" in codes(diags)


def test_moderate_nesting_is_fine() -> None:
    text = "(" * 100 + "1" + ")" * 100
    assert dump(parse_ok(text)) == "1"


def test_unary_minus_binds_to_literals_by_node_not_by_value() -> None:
    expr = parse_ok("-5")
    assert isinstance(expr, Unary) and expr.op == "-"


# -- round trip ---------------------------------------------------------------------------
ROUND_TRIP_SAMPLES = [
    "1 + 2 * 3 - 4 / 5 % 6",
    "not a or b and not c",
    "${approve.decision} == 'approved' and ${load[r].rows} != null",
    "len(${consolidate.rows}) >= 7 or x in y",
    "f(g(1, 'it\\'s'), -2.50)[0].name",
    "- - 1 + (2 - 3) * -4",
    "${approve.decision} in ['approved', 'escalated']",
    "x in [] or [1, [2, 3]][1][0] == 2",
]


@pytest.mark.parametrize("text", ROUND_TRIP_SAMPLES)
def test_round_trip(text: str) -> None:
    first = parse_ok(text)
    again = parse_ok(to_source(first))
    assert dump(first) == dump(again)
    assert to_source(first) == to_source(again)


# -- differential test against Python ---------------------------------------------------------
def _arith(rng: random.Random, depth: int) -> str:
    if depth == 0 or rng.random() < 0.25:
        return str(rng.randint(1, 9))
    op = rng.choice(["+", "-", "*", "/", "%"])
    left, right = _arith(rng, depth - 1), _arith(rng, depth - 1)
    if rng.random() < 0.3:
        left = f"({left})"
    if rng.random() < 0.3:
        right = f"({right})"
    return f"{left} {op} {right}"


def _bool_expr(rng: random.Random, depth: int) -> str:
    if depth == 0 or rng.random() < 0.3:
        cmp_op = rng.choice(["==", "!=", "<", "<=", ">", ">="])
        return f"({_arith(rng, 2)}) {cmp_op} ({_arith(rng, 2)})"
    kind = rng.choice(["and", "or", "not"])
    if kind == "not":
        return f"not ({_bool_expr(rng, depth - 1)})"
    return f"({_bool_expr(rng, depth - 1)}) {kind} ({_bool_expr(rng, depth - 1)})"


_OPS: dict[str, Callable[[Any, Any], Any]] = {
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "/": operator.truediv,
    "%": operator.mod,
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


def evaluate(expr: Expr) -> Any:
    """A tiny reference evaluator for the arithmetic and boolean fragment."""
    if isinstance(expr, Literal):
        return expr.value
    if isinstance(expr, Unary):
        value = evaluate(expr.operand)
        return (not value) if expr.op == "not" else -value
    if isinstance(expr, Binary) and expr.op == "and":
        return evaluate(expr.left) and evaluate(expr.right)
    if isinstance(expr, Binary) and expr.op == "or":
        return evaluate(expr.left) or evaluate(expr.right)
    if isinstance(expr, Binary | Compare):
        return _OPS[expr.op](evaluate(expr.left), evaluate(expr.right))
    raise AssertionError(f"unexpected node {expr!r}")


def test_differential_against_python_on_random_expressions() -> None:
    """Our precedence and associativity give the same value as Python's on 3000 expressions."""
    seed = 20261103
    rng = random.Random(seed)
    checked = 0
    for _ in range(3000):
        text = _bool_expr(rng, 3) if rng.random() < 0.5 else _arith(rng, 4)
        try:
            expected = eval(text)
        except ZeroDivisionError:
            continue
        assert evaluate(parse_ok(text)) == expected, f"seed={seed} text={text!r}"
        checked += 1
    assert checked > 2000


# -- fuzz -----------------------------------------------------------------------------------
def test_property_parser_never_raises_and_always_returns_a_tree() -> None:
    seed = 20261103
    rng = random.Random(seed)
    pieces = [
        "(", ")", "[", "]", ",", ".", "${", "}", "a", "b", "1", "2.5", "'s'", "+", "-",
        "*", "/", "%", "==", "<", "and", "or", "not", "in", "true", "@", " ", "\n",
    ]  # fmt: skip
    for _ in range(3000):
        text = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 25)))
        expr, diags = parse_expression(text)
        assert expr is not None, f"seed={seed} text={text!r}"
        has_error_node = any(isinstance(n, ErrorNode) for n in walk(expr))
        if has_error_node:
            assert diags, f"error node without a diagnostic: seed={seed} text={text!r}"


def test_missing_name_after_dot() -> None:
    expr, diags = parse_expression("a.")
    assert codes(diags) == ["E-EXPR-001"]
    assert dump(expr) == "a"


def test_error_nodes_have_a_text_form() -> None:
    expr, _ = parse_expression("a +")
    assert dump(expr) == "(+ a (error))"
    assert to_source(expr) == "(a + <error>)"


def test_reference_with_unexpected_token_inside() -> None:
    expr, diags = parse_expression("${a b} + 1")
    assert codes(diags) == ["E-EXPR-001"]
    assert dump(expr) == "(+ (error) 1)"


# -- list literal (dsl-spec 3, closes the open point of M1) -----------------------------------
def test_a_list_literal_is_a_primary() -> None:
    assert dump(parse_ok("[1, 'a', true]")) == "(list 1 'a' true)"
    assert dump(parse_ok("[]")) == "(list)"
    assert dump(parse_ok("x in ['a', 'b']")) == "(in x (list 'a' 'b'))"


def test_a_list_literal_can_be_indexed_and_nested() -> None:
    assert dump(parse_ok("[1, 2][0]")) == "(idx (list 1 2) 0)"
    assert dump(parse_ok("[[1], [2, 3]]")) == "(list (list 1) (list 2 3))"


def test_a_bracket_after_a_primary_is_still_an_index() -> None:
    assert dump(parse_ok("x[0]")) == "(idx x 0)"
    assert dump(parse_ok("${a.b}[1]")) == "(idx ${a.b} 1)"


def test_list_elements_are_full_expressions() -> None:
    assert dump(parse_ok("[1 + 2, not a]")) == "(list (+ 1 2) (not a))"


def test_a_trailing_comma_and_a_missing_bracket_are_errors() -> None:
    _, diags = parse_expression("[1, 2,]")
    assert codes(diags) == ["E-EXPR-001"]
    _, diags = parse_expression("[1, 2")
    assert codes(diags) == ["E-EXPR-001"]


def test_recovery_inside_a_list_reports_every_error() -> None:
    _, diags = parse_expression("[1 +, 2 *]")
    assert codes(diags) == ["E-EXPR-001", "E-EXPR-001"]


def test_list_nesting_depth_is_bounded() -> None:
    _, diags = parse_expression("[" * 200 + "]" * 200)
    assert "E-EXPR-006" in codes(diags)
