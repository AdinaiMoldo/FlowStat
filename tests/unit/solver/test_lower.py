"""Tests of the lowering of expressions into predicates (C6, dsl-spec 3.2).

Besides the golden cases there is a differential test: the lowered predicate and the engine's
evaluator are two implementations of the meaning of a guard, and they must agree wherever the
solver claims to know.
"""

from __future__ import annotations

import random

import pytest

from flowstat.dsl.ast import Ref
from flowstat.dsl.parser import parse_expression
from flowstat.engine.evaluator import EvalContext, EvalError, evaluate_bool
from flowstat.solver.domains import Domain
from flowstat.solver.lower import Var, lower
from flowstat.solver.predicate import FALSE, TRUE, Opaque, Pred, holds, nnf, show, variables

DOMAINS = {
    "i": Domain.integer(),
    "j": Domain.integer(),
    "r": Domain.real(),
    "s": Domain.string(),
    "b": Domain.boolean(),
    "d": Domain.enum("approved", "rejected"),
}


def resolve(ref: Ref) -> Var | None:
    names = [str(p.value) for p in ref.path if p.kind == "field"]
    if len(names) == 2 and names[0] == "inputs" and names[1] in DOMAINS:
        return Var(names[1], DOMAINS[names[1]])
    if names == ["approve", "decision"]:
        return Var("d", DOMAINS["d"])
    return None


def low(text: str) -> Pred:
    expr, diags = parse_expression(text)
    assert diags == [], [d.message for d in diags]
    return lower(expr, resolve)


II = "${inputs.i}"
J = "${inputs.j}"
R = "${inputs.r}"
S = "${inputs.s}"
B = "${inputs.b}"
D = "${approve.decision}"


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        # atoms with a literal on either side
        (f"{II} < 5", "i < 5"),
        (f"5 > {II}", "i < 5"),
        (f"{II} >= 5", "i >= 5"),
        (f"5 <= {II}", "i >= 5"),
        (f"{II} == 5", "i == 5"),
        (f"5 != {II}", "i != 5"),
        (f"{R} > 0.1", "r > 1/10"),
        (f"{II} < 2.5", "i < 5/2"),
        (f"{R} == 1000.0", "r == 1000"),
        # sums and differences with a literal
        (f"{II} + 2 > 7", "i > 5"),
        (f"{II} - 2 > 7", "i > 9"),
        (f"2 + {II} > 7", "i > 5"),
        (f"7 < {II} + 2", "i > 5"),
        (f"-{II} < 3", "i > -3"),
        (f"- {II} + 1 <= 4", "i >= -3"),
        # differences of two variables
        (f"{II} - {J} <= 3", "i - j <= 3"),
        (f"{II} <= {J} + 3", "i - j <= 3"),
        (f"{J} + 3 >= {II}", "j - i >= -3"),
        (f"{II} + 1 > {J}", "i - j > -1"),
        (f"{J} - {II} < 2", "j - i < 2"),
        (f"{II} > {J}", "i - j > 0"),
        (f"{II} - {J} + 5 == 2", "i - j == -3"),
        # enumerations and strings
        (f"{D} == 'approved'", "d == 'approved'"),
        (f"'approved' == {D}", "d == 'approved'"),
        (f"{D} != 'approved'", "d != 'approved'"),
        (f"{D} in ['approved', 'escalated']", "d in ['approved', 'escalated']"),
        (f"not ({D} in ['approved'])", "not d in ['approved']"),
        (f"{S} == 'x'", "s == 'x'"),
        # booleans
        (B, "b"),
        (f"not {B}", "not b"),
        (f"{B} == true", "b"),
        (f"{B} == false", "not b"),
        (f"{B} != true", "not b"),
        (f"true == {B}", "b"),
        # numbers in a list
        (f"{II} in [1, 2.0, 3]", "i in [1, 2, 3]"),
        # connectives
        (f"{II} < 5 and {D} == 'approved'", "(i < 5 and d == 'approved')"),
        (f"{II} < 5 or {J} > 2 or {B}", "((i < 5 or j > 2) or b)"),
        (f"not ({II} < 5 or {B})", "not (i < 5 or b)"),
        # literals
        ("1 < 2", "true"),
        ("2 < 1", "false"),
        ("'a' == 'a'", "true"),
        ("'a' != 'a'", "false"),
        ("1 + 1 == 2", "true"),
        ("true", "true"),
        ("false", "false"),
        ("[1, 2] == [1, 2]", "?(([1, 2] == [1, 2]))"),
    ],
)
def test_the_fragment_is_lowered(text: str, shown: str) -> None:
    assert show(low(text)) == shown


@pytest.mark.parametrize(
    "text",
    [
        "len(${inputs.s}) > 0",
        f"{II} + {J} > 3",  # a sum of two variables is not a difference
        f"{II} - {II} > 0 and {II} * {J} > 1",
        f"2 * {II} > 3",
        f"{II} / 2 > 1",
        f"{II} % 2 == 0",
        f"{II} == null",
        f"{S} < 'x'",
        f"{S} > 'x'",
        f"{II} < 'x'",  # the run would fail, it does not branch
        f"{II} < true",
        f"{B} < true",
        f"{D} in {S}",
        f"{II} in [1, {J}]",
        "${nothing.here} == 1",
        f"exists({II})",
        "len([1, 2]) > 1",
        f"{II} + {II} > 2",  # coefficient 2
        f"{B} == {B}",
        f"{R} + 'a' > 1",
    ],
)
def test_everything_else_is_one_opaque_atom(text: str) -> None:
    pred = low(text)
    opaque = _opaques(pred)
    assert opaque >= 1, show(pred)


def _opaques(pred: Pred) -> int:
    text = show(pred)
    return text.count("?(")


def test_the_opaque_text_is_the_source_of_the_atom() -> None:
    assert show(low(f"len({S}) > 0")) == "?((len(${inputs.s}) > 0))"


def test_a_type_mismatch_is_decided() -> None:
    assert low(f"{II} == 'x'") == FALSE
    assert low(f"{II} != 'x'") == TRUE
    assert low(f"{II} == true") == FALSE  # true is not 1
    assert low(f"{II} != true") == TRUE
    assert low(f"{S} == 1") == FALSE
    assert low(f"{B} == 1") == FALSE
    assert low(f"{D} != 3") == TRUE
    assert low(f"{II} == 2.5") != FALSE  # a fraction is a number: the solver decides this one
    assert show(low(f"{II} in [1, 'a', true]")) == "i in [1]"
    assert low(f"{II} in ['a']") == FALSE
    assert low(f"{II} in []") == FALSE


def test_variables_are_named_by_the_resolver() -> None:
    assert variables(low(f"{II} - {J} > 1 and {D} == 'approved'")) == {"i", "j", "d"}


def test_negation_of_an_opaque_atom_stays_opaque() -> None:
    pred = nnf(low(f"not (len({S}) > 0)"))
    assert isinstance(pred, Opaque)


def test_a_syntax_error_node_is_opaque() -> None:
    expr, diags = parse_expression("1 +")
    assert diags
    assert isinstance(lower(expr, resolve), Opaque)


# -- differential test against the engine's evaluator -----------------------------------------
ATOMS = [
    "@I < {a}", "@I >= {a}", "@I == {a}", "@I != {a}", "{a} < @I",
    "@I + {a} > {b}", "@I - @J <= {a}", "@I - @J > {a}", "@I <= @J + {a}",
    "@J - @I != {a}", "@R < {f}", "@R >= {f}", "@R == {f}", "@R - @I < {f}",
    "@R > @J + {f}", "@S == '{t}'", "@S != '{t}'", "@S in ['{t}', '{u}']",
    "@D == '{t}'", "@D in ['{t}', '{u}']", "@B", "not @B", "@B == true", "@B != false",
    "@I == '{t}'", "@I != true", "@S == {a}",
    # opaque: the evaluator knows more than the solver
    "len(@S) > {a}", "@I + @J > {a}", "@I * 2 > {a}", "@I < '{t}'",
    "@I in [{a}, @J]", "@S < '{t}'",
]  # fmt: skip


def _random_text(rng: random.Random, depth: int) -> str:
    if depth == 0 or rng.random() < 0.3:
        text = rng.choice(ATOMS)
        fills = {
            "{a}": str(rng.randint(-4, 4)),
            "{b}": str(rng.randint(-4, 4)),
            "{f}": rng.choice(["0.5", "1.5", "-2.5", "0.0", "2.25"]),
            "{t}": rng.choice(["a", "b", "approved", "rejected"]),
            "{u}": rng.choice(["a", "c", "approved"]),
        }
        fills.update({"@I": II, "@J": J, "@R": R, "@S": S, "@B": B, "@D": D})
        for key, value in fills.items():
            text = text.replace(key, value)
        return text
    roll = rng.random()
    if roll < 0.2:
        return f"not ({_random_text(rng, depth - 1)})"
    op = "and" if roll < 0.6 else "or"
    return f"({_random_text(rng, depth - 1)}) {op} ({_random_text(rng, depth - 1)})"


def test_lowering_agrees_with_the_engine_evaluator() -> None:
    rng = random.Random(20261105)
    compared = decided = 0
    for _ in range(2500):
        text = _random_text(rng, 3)
        expr, diags = parse_expression(text)
        assert diags == []
        pred = lower(expr, resolve)
        values = {
            "i": rng.randint(-4, 4),
            "j": rng.randint(-4, 4),
            "r": rng.choice([0.5, 1.5, -2.5, 0.0, 2.25, -0.5]),
            "s": rng.choice(["a", "b", "approved", "x"]),
            "b": rng.random() < 0.5,
        }
        decision = rng.choice(["approved", "rejected"])
        ctx = EvalContext(inputs=values, outputs={"approve": {"decision": decision}}, instances={})
        assignment: dict[str, object] = {**values, "d": decision}
        mine = holds(pred, assignment)
        try:
            theirs: bool | None = evaluate_bool(expr, ctx)
        except EvalError:
            theirs = None  # the step would fail; the solver makes no claim about runs that fail
        compared += 1
        if mine is not None and theirs is not None:
            decided += 1
            assert mine is theirs, (text, assignment)
    assert compared == 2500
    assert decided > 1200  # most of the random guards are inside the fragment
