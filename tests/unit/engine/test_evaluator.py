"""Tests of the expression evaluator (C3): the runtime meaning of expressions, dsl-spec 3.1.

Golden cases for every operator and every error code, plus a comparison with Python on random
expressions (the oracle) for the fragment on which both languages must agree.
"""

from __future__ import annotations

import contextlib
import random

import pytest

from flowstat.dsl.ast import Expr, Literal, PathPart, Ref
from flowstat.dsl.parser import parse_expression
from flowstat.engine.evaluator import (
    EvalContext,
    EvalError,
    evaluate,
    evaluate_bool,
    render_template,
)

CTX = EvalContext(
    inputs={"quarter": "2025Q1", "limit": 5, "flag": True, "ratio": 0.5},
    outputs={
        "approve": {"decision": "approved"},
        "load[BIS]": {"rows": [1, 2, 3], "name": "Bishkek"},
        "check[BIS]": {"rows": [10]},
        "check[OSC]": {"rows": [20, 21]},
        "check[BAT]": {"rows": []},
        "publish": {"written": 3, "none": None, "nested": {"a": {"b": 7}}},
    },
    instances={"check": ("check[BIS]", "check[OSC]", "check[JAL]", "check[BAT]")},
)


def parse(text: str) -> Expr:
    expr, diags = parse_expression(text, 1, 1)
    assert diags == [], [d.message for d in diags]
    return expr


def run(text: str, ctx: EvalContext = CTX) -> object:
    return evaluate(parse(text), ctx)


def code_of(text: str, ctx: EvalContext = CTX) -> str:
    with pytest.raises(EvalError) as error:
        run(text, ctx)
    return error.value.code


# -- literals and arithmetic ---------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("1", 1),
        ("2.5", 2.5),
        ("'x'", "x"),
        ("true", True),
        ("null", None),
        ("1 + 2 * 3", 7),
        ("(1 + 2) * 3", 9),
        ("7 / 2", 3.5),
        ("8 / 2", 4.0),
        ("7 % 3", 1),
        ("-7 % 3", 2),
        ("-(2 + 3)", -5),
        ("1 + 0.5", 1.5),
        ("'a' + 'b'", "ab"),
        ("2 - 5", -3),
    ],
)
def test_arithmetic_and_literals(text: str, want: object) -> None:
    got = run(text)
    assert got == want and type(got) is type(want)


def test_division_by_zero() -> None:
    assert code_of("1 / 0") == "E-RUN-004"
    assert code_of("1 % 0") == "E-RUN-004"
    assert code_of("1 / (2 - 2)") == "E-RUN-004"


@pytest.mark.parametrize(
    "text", ["1 + 'a'", "'a' - 'b'", "true + 1", "'a' * 2", "-'a'", "null + 1", "1 + null"]
)
def test_arithmetic_type_errors(text: str) -> None:
    assert code_of(text) == "E-RUN-005"


# -- comparison ----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("1 == 1.0", True),
        ("1 == 2", False),
        ("1 != 2", True),
        ("'a' == 'a'", True),
        ("'a' == 1", False),
        ("true == 1", False),
        ("1 == true", False),
        ("null == null", True),
        ("null == 0", False),
        ("1 < 2", True),
        ("2 <= 2", True),
        ("3 > 2.5", True),
        ("2 >= 3", False),
        ("'a' < 'b'", True),
        ("'b' <= 'a'", False),
        ("${check[*].rows} == ${check[*].rows}", True),
        ("${load[BIS].rows} == ${check[BIS].rows}", False),
    ],
)
def test_comparison(text: str, want: bool) -> None:
    assert run(text) is want


@pytest.mark.parametrize("text", ["1 < 'a'", "true < false", "null < 1", "'a' >= 2"])
def test_ordering_type_errors(text: str) -> None:
    assert code_of(text) == "E-RUN-005"


def test_membership() -> None:
    assert run("2 in ${load[BIS].rows}") is True
    assert run("9 in ${load[BIS].rows}") is False
    assert run("'ish' in ${load[BIS].name}") is True
    assert run("'x' in ${load[BIS].name}") is False
    assert run("'rows' in ${load[BIS]}") is True
    assert run("'nope' in ${load[BIS]}") is False
    assert code_of("1 in ${load[BIS].name}") == "E-RUN-005"
    assert code_of("1 in 5") == "E-RUN-005"


# -- boolean operators ---------------------------------------------------------------------------
def test_boolean_operators() -> None:
    assert run("true and false") is False
    assert run("true or false") is True
    assert run("not false") is True
    assert run("1 < 2 and 2 < 3") is True
    assert run("false or 1 > 2 or true") is True


def test_boolean_operators_stop_early() -> None:
    assert run("false and 1 / 0 > 1") is False  # the right side is not evaluated
    assert run("true or 1 / 0 > 1") is True
    assert code_of("true and 1 / 0 > 1") == "E-RUN-004"


@pytest.mark.parametrize(
    "text", ["1 and true", "true and 1", "not 1", "'a' or true", "null and true"]
)
def test_boolean_operators_need_booleans(text: str) -> None:
    assert code_of(text) == "E-RUN-005"


def test_evaluate_bool_needs_a_boolean() -> None:
    assert evaluate_bool(parse("1 < 2"), CTX) is True
    with pytest.raises(EvalError) as error:
        evaluate_bool(parse("1 + 2"), CTX)
    assert error.value.code == "E-RUN-008"


# -- references ----------------------------------------------------------------------------------
def test_references() -> None:
    assert run("${inputs.quarter}") == "2025Q1"
    assert run("${inputs.limit} + 1") == 6
    assert run("${approve.decision} == 'approved'") is True
    assert run("${publish.written} == len(${load[BIS].rows})") is True
    assert run("${publish.nested.a.b}") == 7
    assert run("${publish.none}") is None


def test_unknown_input_and_step_have_no_output() -> None:
    assert code_of("${inputs.nope}") == "E-RUN-002"
    assert code_of("${nostep.x}") == "E-RUN-002"
    assert code_of("${load[OSC].rows}") == "E-RUN-002"  # no output: did not succeed


def test_missing_field_and_null_field() -> None:
    assert code_of("${approve.nope}") == "E-RUN-003"
    assert code_of("${publish.none.x}") == "E-RUN-003"
    assert code_of("${publish.written.x}") == "E-RUN-003"
    assert code_of("${inputs.quarter.x}") == "E-RUN-003"


def test_fan_in_reads_the_instances_that_succeeded_in_declaration_order() -> None:
    assert run("${check[*].rows}") == [[10], [20, 21], []]  # JAL has no output: left out
    assert run("len(${check[*].rows})") == 3
    assert run("${check[*]}") == [{"rows": [10]}, {"rows": [20, 21]}, {"rows": []}]


def test_fan_in_of_nothing_is_an_empty_list() -> None:
    empty = EvalContext(inputs={}, outputs={}, instances={"check": ("check[A]", "check[B]")})
    assert evaluate(parse("${check[*].rows}"), empty) == []


def test_fan_in_with_a_missing_field_is_an_error() -> None:
    assert code_of("${check[*].nope}") == "E-RUN-003"


def test_attr_and_index_on_values() -> None:
    assert run("${load[BIS].rows}[1]") == 2
    assert run("${load[BIS]}['name']") == "Bishkek"
    assert run("${load[BIS]}.name") == "Bishkek"
    assert code_of("${load[BIS].rows}[3]") == "E-RUN-006"
    assert code_of("${load[BIS].rows}[-1]") == "E-RUN-006"
    assert code_of("${load[BIS].rows}['a']") == "E-RUN-006"
    assert code_of("${load[BIS]}['zzz']") == "E-RUN-006"
    assert code_of("${load[BIS].name}[0]") == "E-RUN-006"
    assert code_of("${load[BIS].rows}.x") == "E-RUN-003"


def test_unknown_name_and_function() -> None:
    assert code_of("nothing") == "E-RUN-001"
    assert code_of("nothing(1)") == "E-RUN-001"
    assert code_of("(1)(2)") == "E-RUN-001"


# -- built-in functions --------------------------------------------------------------------------
def test_len() -> None:
    assert run("len(${load[BIS].rows})") == 3
    assert run("len('abc')") == 3
    assert run("len(${load[BIS]})") == 2
    assert code_of("len(5)") == "E-RUN-005"


def test_sum_min_max() -> None:
    assert run("sum(${load[BIS].rows})") == 6
    assert run("sum(${check[BAT].rows})") == 0
    assert run("min(${load[BIS].rows})") == 1
    assert run("max(${load[BIS].rows})") == 3
    assert run("max(${check[*].rows}[0])") == 10
    assert code_of("min(${check[BAT].rows})") == "E-RUN-007"
    assert code_of("sum(${load[BIS].name})") == "E-RUN-005"
    assert code_of("sum(${check[*].rows})") == "E-RUN-005"
    sums = EvalContext(inputs={"xs": [1, 2.5]}, outputs={}, instances={})
    assert evaluate(parse("sum(${inputs.xs})"), sums) == 3.5
    words = EvalContext(inputs={"xs": ["b", "a"]}, outputs={}, instances={})
    assert evaluate(parse("min(${inputs.xs})"), words) == "a"
    mixed = EvalContext(inputs={"xs": [1, "a"]}, outputs={}, instances={})
    with pytest.raises(EvalError) as error:
        evaluate(parse("max(${inputs.xs})"), mixed)
    assert error.value.code == "E-RUN-005"


def test_count_ignores_null() -> None:
    ctx = EvalContext(inputs={"xs": [1, None, 3, None]}, outputs={}, instances={})
    assert evaluate(parse("count(${inputs.xs})"), ctx) == 2
    assert code_of("count(5)") == "E-RUN-005"


def test_exists_is_a_guard() -> None:
    assert run("exists(${approve.decision})") is True
    assert run("exists(${load[OSC].rows})") is False  # the step has no output
    assert run("exists(${approve.nope})") is False
    assert run("exists(${publish.none})") is False  # null
    assert run("exists(${inputs.nope})") is False
    assert run("exists(${load[BIS].rows}[1])") is True
    assert run("exists(${load[BIS].rows}[9])") is False  # out of range
    assert run("exists(${approve.decision}) and ${approve.decision} == 'approved'") is True
    assert run("exists(${load[OSC].rows}) and ${load[OSC].rows} == 1") is False


def test_exists_does_not_hide_other_errors() -> None:
    assert code_of("exists(${load[BIS].rows}[1 / 0])") == "E-RUN-004"


@pytest.mark.parametrize(
    "text", ["len()", "len(1, 2)", "sum()", "exists()", "exists(1, 2)", "count(1, 2)"]
)
def test_wrong_number_of_arguments(text: str) -> None:
    assert code_of(text) == "E-RUN-007"


def test_exists_needs_a_reference() -> None:
    assert code_of("exists(1)") == "E-RUN-007"


# -- positions -----------------------------------------------------------------------------------
def test_error_carries_the_position_of_the_expression_part() -> None:
    expr, _ = parse_expression("1 +\n  2 / 0", 10, 5)
    with pytest.raises(EvalError) as error:
        evaluate(expr, CTX)
    assert (error.value.line, error.value.column) == (11, 3)


# -- templates -----------------------------------------------------------------------------------
def ref(*names: str) -> Ref:
    return Ref(1, 1, tuple(PathPart("field", n) for n in names))


def test_template_with_one_reference_keeps_the_type() -> None:
    assert render_template((ref("load[BIS]", "rows"),), CTX) == [1, 2, 3]
    assert render_template((ref("inputs", "limit"),), CTX) == 5
    assert render_template((ref("inputs", "flag"),), CTX) is True


def test_template_with_text_joins_the_parts() -> None:
    parts = ("BIS", "/", ref("inputs", "quarter"), ".xlsx")
    assert render_template(parts, CTX) == "BIS/2025Q1.xlsx"
    assert render_template(("n=", ref("inputs", "limit")), CTX) == "n=5"
    assert render_template(("ok=", ref("inputs", "flag")), CTX) == "ok=true"
    assert render_template(("r=", ref("inputs", "ratio")), CTX) == "r=0.5"


def test_template_with_a_plain_value_or_plain_text() -> None:
    assert render_template(("chief_statistician",), CTX) == "chief_statistician"
    assert render_template((5,), CTX) == 5
    assert render_template((), CTX) == ""


def test_template_cannot_join_null_lists_or_records() -> None:
    for names in (("publish", "none"), ("load[BIS]", "rows"), ("load[BIS]",)):
        with pytest.raises(EvalError) as error:
            render_template(("x", ref(*names)), CTX)
        assert error.value.code in {"E-RUN-003", "E-RUN-005"}
    with pytest.raises(EvalError) as error:
        render_template(("x", ref("publish", "none")), CTX)
    assert error.value.code == "E-RUN-005"


def test_template_reference_error_is_reported() -> None:
    with pytest.raises(EvalError) as error:
        render_template((ref("inputs", "nope"),), CTX)
    assert error.value.code == "E-RUN-002"


# -- the oracle: random expressions compared with Python -----------------------------------------
def gen_number(rng: random.Random, depth: int) -> str:
    if depth == 0 or rng.random() < 0.3:
        return (
            str(rng.randint(0, 9))
            if rng.random() < 0.7
            else f"{rng.randint(0, 9)}.{rng.randint(0, 9)}"
        )
    kind = rng.random()
    if kind < 0.15:
        return f"(-{gen_number(rng, depth - 1)})"
    op = rng.choice(["+", "-", "*", "/", "%"])
    return f"({gen_number(rng, depth - 1)} {op} {gen_number(rng, depth - 1)})"


def gen_bool(rng: random.Random, depth: int) -> str:
    if depth == 0 or rng.random() < 0.25:
        return rng.choice(["true", "false"])
    kind = rng.random()
    if kind < 0.35:
        op = rng.choice(["==", "!=", "<", "<=", ">", ">="])
        return f"({gen_number(rng, depth - 1)} {op} {gen_number(rng, depth - 1)})"
    if kind < 0.5:
        return f"(not {gen_bool(rng, depth - 1)})"
    op = rng.choice(["and", "or"])
    return f"({gen_bool(rng, depth - 1)} {op} {gen_bool(rng, depth - 1)})"


def python_value(text: str) -> object:
    source = text.replace("true", "True").replace("false", "False")
    return eval(source)  # the generator builds the text, nothing else


@pytest.mark.parametrize("kind", ["number", "bool"])
def test_random_expressions_agree_with_python(kind: str) -> None:
    rng = random.Random(20261105)
    gen = gen_number if kind == "number" else gen_bool
    zero_divisions = 0
    for _ in range(3000):
        text = gen(rng, 4)
        try:
            want = python_value(text)
        except ZeroDivisionError:
            zero_divisions += 1
            assert code_of(text) == "E-RUN-004", text
            continue
        got = run(text)
        assert got == want and type(got) is type(want), (text, got, want)
    assert zero_divisions < 3000


def test_random_expressions_never_raise_anything_but_eval_error() -> None:
    rng = random.Random(7)
    pieces = [
        "1",
        "'a'",
        "true",
        "null",
        "${approve.decision}",
        "${check[*].rows}",
        "len(",
        ")",
        "+",
        "and",
        "<",
        "in",
        "-",
        "not",
        "[0]",
        ".x",
        "exists(",
    ]
    for _ in range(3000):
        text = " ".join(rng.choice(pieces) for _ in range(rng.randint(1, 8)))
        expr, diags = parse_expression(text, 1, 1)
        if diags:
            continue
        with contextlib.suppress(EvalError):
            evaluate(expr, CTX)


def test_literal_nodes_are_values() -> None:
    assert evaluate(Literal(1, 1, 5), CTX) == 5


def test_remaining_corners() -> None:
    from flowstat.dsl.ast import ErrorNode

    assert code_of("1.5 + 'a'") == "E-RUN-005"  # the message names the kinds
    assert code_of("${approve[0]}") == "E-RUN-001"  # an index is not a path of a step output
    assert code_of("${inputs}") == "E-RUN-002"
    assert run("${load[BIS]} == ${load[BIS]}") is True
    assert run("${load[BIS]} == ${approve}") is False
    assert code_of("min(5)") == "E-RUN-005"
    assert code_of("max(${load[BIS].rows}[0])") == "E-RUN-005"
    assert render_template(("a", 5), CTX) == "a5"
    with pytest.raises(EvalError) as error:
        evaluate(ErrorNode(1, 1, "boom"), CTX)
    assert error.value.code == "E-RUN-001"
