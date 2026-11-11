"""Tests of the type check (C1 phase 4, US-18 and US-19): docs/dsl-spec.md, sections 4 and 9."""

from __future__ import annotations

import random

import pytest

from flowstat.actions.registry import default_registry
from flowstat.analysis.types import (
    ANY,
    FLOAT,
    ROW,
    ListT,
    RecordT,
    assignable,
    check_types,
    parse_type,
    show,
)
from flowstat.model.diagnostics import Diagnostic, Severity
from tests.reference.interp import Fail, value
from tests.unit.analysis.helpers import INPUTS, compile_plan, step

SIGNATURES = default_registry().signatures()
LOAD = "  load:\n    action: xlsx.read\n    with: {path: 'BIS/q.xlsx'}\n"


def check(steps: str, goal: str = "z", inputs: str = INPUTS) -> list[Diagnostic]:
    return check_types(compile_plan(steps, goal, inputs), SIGNATURES)


def lines_of(steps: str, inputs: str = INPUTS) -> list[str]:
    """The lines of the whole document that ``compile_plan`` builds, for positions."""
    head = "workflow: demo\nversion: 1\n" + ("inputs:\n" + inputs if inputs else "") + "steps:\n"
    return (head + steps).splitlines()


def codes(items: list[Diagnostic]) -> list[str]:
    return [d.code for d in items]


# -- the types --------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    ["int", "string", "list<row>", "list<list<row>>", "record{a: int, b: list<string>}", "any"],
)
def test_types_are_written_and_read_back(text: str) -> None:
    assert show(parse_type(text)) == text
    assert parse_type(show(parse_type(text))) == parse_type(text)


@pytest.mark.parametrize("text", ["lst<int>", "list<int", "record{a int}", "int extra", ""])
def test_a_bad_type_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match=r"type|missing|unexpected"):
        parse_type(text)


def test_the_row_is_a_record_of_three_fields() -> None:
    assert parse_type("row") == ROW
    assert isinstance(ROW, RecordT) and ROW.get("value") == FLOAT and ROW.get("none") is None


@pytest.mark.parametrize(
    ("source", "target", "fits"),
    [
        ("int", "float", True),
        ("float", "int", False),
        ("string", "int", False),
        ("any", "int", True),
        ("int", "any", True),
        ("list<int>", "list<float>", True),
        ("list<float>", "list<int>", False),
        ("list<row>", "list<list<row>>", False),
        ("list<list<row>>", "list<list<row>>", True),
        ("record{a: int, b: string}", "record{a: float}", True),  # more fields is a subtype
        ("record{a: int}", "record{a: int, b: string}", False),
        ("list<any>", "list<row>", True),
        ("bool", "int", False),
    ],
)
def test_assignable(source: str, target: str, fits: bool) -> None:
    assert assignable(parse_type(source), parse_type(target)) is fits


# -- US-18: the example of the story ----------------------------------------------------------
def test_a_list_compared_with_a_number_is_one_error_at_its_column() -> None:
    steps = LOAD + step("z", "${load.rows} > 5", ["load"])
    got = check(steps)
    assert codes(got) == ["E-TYPE-001"]  # no follow-up errors
    error = got[0]
    assert error.severity is Severity.ERROR
    assert error.message.startswith("A list cannot be compared with a number")
    line = lines_of(steps)[error.line - 1]
    assert line[error.column - 1 :].startswith("${load.rows} > 5")


def test_an_error_does_not_cascade_into_the_condition() -> None:
    # the comparison is wrong, but it is still a boolean, so the 'and' and the step are fine
    got = check(LOAD + step("z", "${load.rows} > 5 and ${inputs.flag}", ["load"]))
    assert codes(got) == ["E-TYPE-001"]


def test_a_wrong_operand_gives_any_and_stops_there() -> None:
    got = check(LOAD + step("z", "(${load.rows} + 1) * 2 > 3", ["load"]))
    assert codes(got) == ["E-TYPE-001"]
    assert "'+' needs" in got[0].message


# -- conditions and operators -----------------------------------------------------------------
@pytest.mark.parametrize(
    "guard",
    [
        "${inputs.amount} > 5",
        "${inputs.amount} + 1 > ${inputs.ratio} * 2",
        "${inputs.mode} == 'fast' or not ${inputs.flag}",
        "${inputs.mode} in ['fast', 'slow']",
        "'ast' in ${inputs.mode}",
        "${inputs.mode} < 'z'",
        "-${inputs.amount} < 0",
        "${inputs.amount} / 2 > 1.5",
        "${inputs.amount} % 2 == 0",
        "len(${inputs.mode}) > 2",
        "${inputs.amount} in [1, 2.5]",
        "${inputs.amount} == null",
        "exists(${load.rows}) and len(${load.rows}) > 0",
        "sum([1, 2, 3]) > 2 and min([1, 2]) < max([3, 4])",
        "count([1, null]) == 1",
        "someday(${inputs.amount}) > 1",  # a function that is not built in: valid, unknown (3)
        "${load.rows}[0].value > 1.5",
        "${load.rows}[0].code == 'A'",
    ],
)
def test_well_typed_guards_have_no_finding(guard: str) -> None:
    assert check(LOAD + step("z", guard, ["load"])) == []


@pytest.mark.parametrize(
    ("guard", "fragment"),
    [
        ("${inputs.amount}", "'when' must be true or false, not a number"),
        ("${inputs.mode} > 3", "A string cannot be compared with a number"),
        ("${inputs.flag} < ${inputs.flag}", "A boolean cannot be compared with a boolean"),
        ("${inputs.amount} and ${inputs.flag}", "'and' needs true or false, not a number"),
        ("not ${inputs.mode}", "'not' needs true or false, not a string"),
        ("-${inputs.mode} > 1", "'-' needs a number, not a string"),
        ("${inputs.mode} + 1 > 2", "'+' needs two numbers or two strings"),
        ("${inputs.mode} * 2 > 2", "'*' needs two numbers"),
        ("${inputs.amount} in ${inputs.ratio}", "'in' needs a list, a string or a record"),
        ("${inputs.amount} in ${inputs.mode}", "'in' with a string on the right needs a string"),
        ("len(${inputs.amount}) > 1", "'len' needs a list, a string or a record"),
        ("sum(${inputs.mode}) > 1", "'sum' needs a list of numbers"),
        ("sum(['a']) > 1", "'sum' needs a list of numbers"),
        ("max([true]) > 1", "'max' needs a list of numbers or of strings"),
        ("count(${inputs.amount}) > 1", "'count' needs a list"),
        ("len() > 1", "'len' takes one argument"),
        ("exists() and true", "'exists' takes one argument"),
        ("${load.rows}[${inputs.mode}].value > 1", "a list is indexed by a whole number"),
        ("${inputs.amount}[0] > 1", "a number cannot be indexed"),
    ],
)
def test_ill_typed_guards_are_errors(guard: str, fragment: str) -> None:
    got = check(LOAD + step("z", guard, ["load"]))
    assert [d.code for d in got] == ["E-TYPE-001"], got
    assert fragment in got[0].message


def test_equality_between_unrelated_types_is_a_warning_and_not_an_error() -> None:
    got = check(step("z", "${inputs.amount} == 'five'"))
    assert [(d.code, d.severity) for d in got] == [("W-TYPE-005", Severity.WARNING)]
    assert "always false" in got[0].message
    assert "always true" in check(step("z", "${inputs.amount} != 'five'"))[0].message
    assert check(step("z", "${inputs.amount} == 5.0")) == []  # int and float meet
    assert check(step("z", "${inputs.flag} == 1"))[0].code == "W-TYPE-005"  # true is not 1


def test_ensures_is_a_condition_too_and_sees_its_own_step() -> None:
    steps = '  z:\n    action: db.write\n    with: {rows: []}\n    ensures: "${z.written} > 0"\n'
    assert check(steps) == []
    bad = steps.replace("${z.written} > 0", "${z.batch}")
    assert "'ensures' must be true or false, not a string" in check(bad)[0].message


# -- references -------------------------------------------------------------------------------
def test_a_field_the_action_does_not_have() -> None:
    got = check(LOAD + step("z", "${load.nothing} > 1", ["load"]))
    assert codes(got) == ["E-TYPE-002"]
    assert "rows" in got[0].message and "count" in got[0].message  # it names what exists


def test_exists_may_read_a_field_that_is_not_there() -> None:
    assert check(LOAD + step("z", "exists(${load.nothing})", ["load"])) == []


def test_fields_of_a_record_and_of_a_row() -> None:
    got = check(LOAD + step("z", "${load.rows}[0].colour == 'red'", ["load"]))
    assert codes(got) == ["E-TYPE-002"] and "no field 'colour'" in got[0].message
    got = check(LOAD + step("z", "${load.count}.x > 1", ["load"]))
    assert "a number has no field 'x'" in got[0].message


def test_the_decision_of_an_approval_is_a_string() -> None:
    approve = "  approve:\n    action: approval.wait\n    with: {role: chief}\n"
    assert check(approve + step("z", "${approve.decision} == 'approved'", ["approve"])) == []
    got = check(approve + step("z", "${approve.decision} > 3", ["approve"]))
    assert "A string cannot be compared with a number" in got[0].message
    assert check(approve + step("z", "${approve.by} == 'x'", ["approve"])) == []


def test_an_input_has_its_declared_type() -> None:
    inputs = "  names: {type: list<string>}\n  when: {type: date}\n  n: {type: int}\n"
    assert check(step("z", "'a' in ${inputs.names}"), inputs=inputs) == []
    assert check(step("z", "${inputs.when} < ${inputs.when}"), inputs=inputs) == []
    got = check(step("z", "${inputs.when} < 3"), inputs=inputs)
    assert "A date cannot be compared with a number" in got[0].message
    got = check(step("z", "${inputs.names} == 3"), inputs=inputs)
    assert got[0].code == "W-TYPE-005"


# -- US-19: fan-in and arguments --------------------------------------------------------------
FAN_IN = (
    "  load:\n    foreach: region in catalogue.regions\n    action: xlsx.read\n"
    '    with: {path: "${region.code}/q.xlsx"}\n    on_failure: continue\n'
    "  check:\n    foreach: region in catalogue.regions\n    action: statguard.validate\n"
    '    needs: ["load[region]"]\n    with: {rows: "${load[region].rows}"}\n'
)
CONSOLIDATE = (
    '  consolidate:\n    action: stats.aggregate\n    needs: ["check[*]"]\n'
    '    join: {at_least: 1}\n    with: {parts: "${check[*].rows}"}\n'
)


def test_a_fan_in_reference_is_a_list_of_what_one_instance_gives() -> None:
    # check[*].rows is list<list<row>> and that is what stats.aggregate asks for
    assert check(FAN_IN + CONSOLIDATE, "consolidate") == []


def test_an_action_that_wants_one_rows_value_refuses_the_fan_in() -> None:
    steps = FAN_IN + CONSOLIDATE.replace("stats.aggregate", "db.write").replace("parts", "rows")
    got = check(steps, "consolidate")
    assert codes(got) == ["E-TYPE-001"]
    assert "'rows' of db.write must be list<row>, not list<list<row>>" in got[0].message
    line = lines_of(steps)[got[0].line - 1]  # at the reference
    assert line[got[0].column - 1 :].startswith("${check[*].rows}")


def test_a_foreach_step_is_checked_once() -> None:
    steps = FAN_IN.replace("${load[region].rows}", "${load[region].count}") + CONSOLIDATE
    got = check(steps, "consolidate")
    assert codes(got) == ["E-TYPE-001"]  # nine instances, one message
    assert "must be list<row>, not int" in got[0].message


def test_each_argument_is_checked_against_the_declared_type() -> None:
    assert check("  z:\n    action: xlsx.read\n    with: {path: 'A/q.xlsx'}\n") == []
    got = check("  z:\n    action: xlsx.read\n    with: {path: 5}\n")
    assert "'path' of xlsx.read must be string, not int" in got[0].message
    got = check("  z:\n    action: db.write\n    with: {rows: [1, 2]}\n")
    assert "must be list<row>, not list<int>" in got[0].message
    ok = "  z:\n    action: db.write\n    with: {rows: [{code: A, name: a, value: 1}]}\n"
    assert check(ok) == []  # an int where a float is wanted is fine
    assert check("  z:\n    action: db.write\n    with: {rows: []}\n") == []  # list<any>


def test_a_template_with_text_is_a_string_and_its_parts_must_be_scalars() -> None:
    inputs = INPUTS + "  names: {type: list<string>}\n"
    ok = "  z:\n    action: xlsx.read\n    with: {path: 'a/${inputs.mode}/${inputs.amount}.x'}\n"
    assert check(ok, inputs=inputs) == []
    bad = "  z:\n    action: xlsx.read\n    with: {path: 'a/${inputs.names}.x'}\n"
    got = check(bad, inputs=inputs)
    assert "a list cannot be part of a text" in got[0].message


def test_a_missing_argument_and_an_argument_that_is_not_taken() -> None:
    got = check("  z:\n    action: xlsx.read\n")
    assert [(d.code, d.severity) for d in got] == [("E-TYPE-003", Severity.ERROR)]
    assert "needs the argument 'path'" in got[0].message
    got = check("  z:\n    action: xlsx.read\n    with: {path: 'a', sheet: 2}\n")
    assert [(d.code, d.severity) for d in got] == [("W-TYPE-004", Severity.WARNING)]


def test_optional_arguments_and_the_decisions_of_an_approval() -> None:
    http = "  z:\n    action: http.call\n    with: {url: 'http://x', method: GET, body: {a: 1}}\n"
    assert check(http) == []
    approve = "  z:\n    action: approval.wait\n    with: {role: c, decisions: [a, b]}\n"
    assert check(approve) == []
    assert "list<string>" in check(approve.replace("[a, b]", "5"))[0].message


def test_an_action_without_a_signature_is_not_checked() -> None:
    assert check("  z:\n    action: x.y\n    with: {anything: 1}\n") == []


def test_the_example_workflow_has_no_finding() -> None:
    from pathlib import Path

    from flowstat.compiler.catalogue import Catalogue
    from flowstat.compiler.compile import compile_workflow

    root = Path(__file__).resolve().parents[3] / "examples"
    text = (root / "quarterly_consolidation.yaml").read_text(encoding="utf8")
    regions = tuple({"code": c, "name": c} for c in "ABCDEFGHI")
    plan = compile_workflow(text, Catalogue({"regions": regions})).plan
    assert plan is not None and check_types(plan, SIGNATURES) == []


# -- the declarations agree with the registry ------------------------------------------------
def test_the_declared_types_agree_with_the_python_types_the_registry_checks() -> None:
    from flowstat.actions.registry import default_registry as registry

    reg = registry()
    kinds = {"string": str, "int": int, "float": float, "bool": bool}
    specs = reg._specs  # the registry's own table
    for name, spec in specs.items():
        sig = reg.signatures()[name]
        for field, python in spec.params.items():
            declared = parse_type(sig.params[field])
            expected = list if isinstance(declared, ListT) else kinds.get(show(declared))
            assert expected is python or declared == ANY, (name, field)
        for field, python in (spec.output or {}).items():
            assert sig.outputs is not None
            declared = parse_type(sig.outputs[field])
            expected = list if isinstance(declared, ListT) else kinds.get(show(declared))
            assert expected is python or declared == ANY, (name, field)


# -- sound on random expressions, against the reference evaluator ----------------------------
def _gen(rng: random.Random, kind: str, depth: int) -> str:
    """A random expression meant to have type ``kind``; now and then a wrong one on purpose."""
    if depth <= 0 or rng.random() < 0.25:
        leaves = {
            "num": ["${inputs.amount}", "${inputs.ratio}", "3", "0.5", "7"],
            "str": ["${inputs.mode}", "'fast'", "'slow'", "'x'"],
            "bool": ["${inputs.flag}", "true", "false"],
        }
        return rng.choice(leaves[kind])
    if rng.random() < 0.12:  # a mistake: any kind
        return _gen(rng, rng.choice(["num", "str", "bool"]), depth - 1)
    sub = depth - 1
    if kind == "num":
        op = rng.choice(["+", "-", "*", "neg", "len", "/", "%"])
        if op == "neg":
            return f"(-{_gen(rng, 'num', sub)})"
        if op == "len":
            return f"len({_gen(rng, 'str', sub)})"
        if op in ("/", "%"):
            return f"({_gen(rng, 'num', sub)} {op} {rng.choice(['2', '4', '0.5'])})"
        return f"({_gen(rng, 'num', sub)} {op} {_gen(rng, 'num', sub)})"
    if kind == "str":
        return f"({_gen(rng, 'str', sub)} + {_gen(rng, 'str', sub)})"
    op = rng.choice(["cmp", "eq", "and", "or", "not", "in", "inlist", "ordstr"])
    if op == "cmp":
        order = rng.choice(["<", "<=", ">", ">="])
        return f"({_gen(rng, 'num', sub)} {order} {_gen(rng, 'num', sub)})"
    if op == "ordstr":
        return f"({_gen(rng, 'str', sub)} {rng.choice(['<', '>='])} {_gen(rng, 'str', sub)})"
    if op == "eq":
        kind2 = rng.choice(["num", "str", "bool"])
        return f"({_gen(rng, kind2, sub)} {rng.choice(['==', '!='])} {_gen(rng, kind2, sub)})"
    if op in ("and", "or"):
        return f"({_gen(rng, 'bool', sub)} {op} {_gen(rng, 'bool', sub)})"
    if op == "not":
        return f"(not {_gen(rng, 'bool', sub)})"
    if op == "in":
        return f"({_gen(rng, 'str', sub)} in {_gen(rng, 'str', sub)})"
    return f"({_gen(rng, 'num', sub)} in [1, 2.5, {_gen(rng, 'num', sub)}])"


GRID = [
    {"inputs.amount": a, "inputs.mode": m, "inputs.ratio": r, "inputs.flag": f}
    for a in (0, 3, 50)
    for m in ("fast", "off")
    for r in (0.0, 0.5)
    for f in (True, False)
]


@pytest.mark.parametrize("seed", range(4))
def test_the_checker_agrees_with_the_reference_evaluator(seed: int) -> None:
    """Typed means it never fails for a type reason; a type failure at run time was reported."""
    rng = random.Random(77 + seed)
    accepted = rejected = 0
    for _ in range(150):
        text = _gen(rng, "bool", 4)
        plan = compile_plan(step("z", text), "z")
        errors = [d for d in check_types(plan, SIGNATURES) if d.severity is Severity.ERROR]
        failed = False
        assert plan.steps[0].when is not None
        for values in GRID:
            try:
                value(plan.steps[0].when, values, frozenset())
            except Fail:
                failed = True
        if errors:
            rejected += 1
        else:
            accepted += 1
            assert not failed, text  # the checker let through an expression that fails
        if failed:
            assert errors, text  # a failure for a type reason that the checker did not report
    assert accepted > 40 and rejected > 25  # both kinds are well represented


# -- the time limit of a step (US-38, dsl-spec 5.6) ---------------------------------------------
ASK = "  ask:\n    action: approval.wait\n    with: {role: chief}\n    timeout: 3600\n"
BUSY = "  work:\n    action: xlsx.read\n    with: {path: 'BIS/q.xlsx'}\n    timeout: 3600\n"


def test_a_time_limit_on_a_step_that_waits_for_a_person_is_accepted() -> None:
    assert "E-TYPE-006" not in codes(check(ASK, "ask"))


def test_a_time_limit_on_a_step_that_does_not_wait_is_an_error_at_the_step() -> None:
    [found] = [d for d in check(BUSY, "work") if d.code == "E-TYPE-006"]
    assert found.severity is Severity.ERROR
    assert "xlsx.read does not wait" in found.message
    line = lines_of(BUSY).index("  work:") + 1
    assert (found.line, found.column) == (line, 3)  # the line of the step, as for W-TYPE-004


def test_the_limit_is_judged_once_for_every_instance_of_a_foreach() -> None:
    steps = FAN_IN.replace("    action: xlsx.read\n", "    action: xlsx.read\n    timeout: 10\n", 1)
    steps += CONSOLIDATE
    assert codes(check(steps, "consolidate")).count("E-TYPE-006") == 1  # nine instances, one error


def test_an_action_the_checker_does_not_know_is_not_judged() -> None:
    steps = "  x:\n    action: custom.thing\n    timeout: 5\n"
    assert "E-TYPE-006" not in codes(check(steps, "x"))


# -- the hosts http.call may reach (US-39) ---------------------------------------------------------
HOSTS = frozenset({"api.example.kg"})
LISTED = default_registry(HOSTS).signatures()


def call_step(url: str, hosts_given: bool = True) -> list[Diagnostic]:
    steps = f"  z:\n    action: http.call\n    with: {{url: {url}}}\n"
    return check_types(compile_plan(steps, "z", ""), LISTED if hosts_given else SIGNATURES)


def test_a_constant_url_on_the_list_is_accepted() -> None:
    assert call_step("'https://api.example.kg/v1/pay'") == []


def test_a_constant_url_outside_the_list_is_an_error_that_names_the_host_and_the_list() -> None:
    [found] = call_step("'https://other.example.kg/v1'")
    assert found.code == "E-TYPE-007" and found.severity is Severity.ERROR
    assert "other.example.kg" in found.message and "api.example.kg" in found.message


def test_without_a_list_any_host_is_accepted() -> None:
    assert call_step("'https://other.example.kg/v1'", hosts_given=False) == []


def test_a_url_whose_host_is_built_from_a_reference_is_not_judged_here() -> None:
    steps = "  z:\n    action: http.call\n    with: {url: 'https://${inputs.mode}.example.kg/v1'}\n"
    plan = compile_plan(steps, "z", INPUTS)
    assert "E-TYPE-007" not in codes(check_types(plan, LISTED))


def test_a_url_whose_host_is_written_and_whose_path_is_a_reference_is_judged() -> None:
    steps = (
        "  z:\n    action: http.call\n    with: {url: 'https://other.example.kg/${inputs.mode}'}\n"
    )
    plan = compile_plan(steps, "z", INPUTS)
    assert codes(check_types(plan, LISTED)) == ["E-TYPE-007"]
