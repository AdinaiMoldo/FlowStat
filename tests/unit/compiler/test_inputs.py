"""US-02: inputs of a workflow, ``ref`` to a catalogue list, and validation of the values."""

from __future__ import annotations

import pytest

from flowstat.compiler.catalogue import Catalogue
from flowstat.compiler.compile import compile_workflow
from flowstat.compiler.inputs import validate_inputs
from flowstat.compiler.plan import Plan
from tests.unit.markers import MARK, split_markers

CAT = Catalogue(
    {
        "regions": tuple({"code": c, "name": c.lower()} for c in ("BIS", "OSC", "BAT")),
        "forms": ({"code": "F1"}, {"code": "F2"}),
    }
)


def doc(inputs: str) -> str:
    return (
        "workflow: demo\nversion: 1\ninputs:\n" + inputs
        + "steps:\n  a:\n    action: x.y\ngoals: {any_of: [a]}\n"
    )  # fmt: skip


def plan_of(inputs: str) -> Plan:
    result = compile_workflow(doc(inputs), CAT)
    assert result.diagnostics == [], [d.message for d in result.diagnostics]
    assert result.plan is not None
    return result.plan


# -- compilation ---------------------------------------------------------------------------------
def test_plan_carries_the_input_specs() -> None:
    plan = plan_of(
        "  region: {type: string, ref: regions}\n"
        "  quarter: {type: string, pattern: '^20[0-9]{2}Q[1-4]$', default: 2025Q1}\n"
        "  limit: {type: int, required: false}\n"
    )
    region, quarter, limit = plan.inputs
    assert (region.name, region.type, region.ref) == ("region", "string", "regions")
    assert quarter.pattern == "^20[0-9]{2}Q[1-4]$" and quarter.has_default
    assert quarter.default == "2025Q1"
    assert limit.required is False and not limit.has_default and limit.ref is None
    assert (region.line, region.column) == (4, 3)


def test_plan_without_inputs_has_none() -> None:
    result = compile_workflow(
        "workflow: d\nversion: 1\nsteps:\n  a:\n    action: x.y\ngoals: {any_of: [a]}\n", CAT
    )
    assert result.plan is not None and result.plan.inputs == ()


ERRORS: list[tuple[str, str, list[str]]] = [
    ("unknown list", "  r: {type: string, ref: " + MARK + "nothing}\n", ["E-REF-012"]),
    ("ref on an int", "  r: {type: int, ref: " + MARK + "regions}\n", ["E-REF-014"]),
    ("bad pattern", "  r: {type: string, pattern: '" + MARK + "['}\n", ["E-SHAPE-004"]),
    ("ref on a list of ints", "  r: {type: list<int>, ref: " + MARK + "regions}\n", ["E-REF-014"]),
]


@pytest.mark.parametrize(("name", "inputs", "codes"), ERRORS, ids=[e[0] for e in ERRORS])
def test_input_errors(name: str, inputs: str, codes: list[str]) -> None:
    clean, expected = split_markers(doc(inputs))
    result = compile_workflow(clean, CAT)
    got = [(d.code, d.line, d.column) for d in result.diagnostics]
    want = [(c, *pos) for c, pos in zip(codes, expected, strict=True)]
    assert got == want, [d.message for d in result.diagnostics]
    assert result.plan is None


def test_ref_to_a_list_of_strings_is_allowed() -> None:
    plan_of("  r: {type: 'list<string>', ref: regions}\n")


# -- validation of values ------------------------------------------------------------------------
def validate(inputs: str, values: dict[str, object]) -> tuple[dict[str, object], list[str]]:
    resolved, errors = validate_inputs(plan_of(inputs), values, CAT)
    return resolved, [e.code for e in errors]


def test_us02_scenario_accepts_only_catalogue_codes() -> None:
    spec = "  region: {type: string, ref: regions}\n"
    assert validate(spec, {"region": "BIS"}) == ({"region": "BIS"}, [])
    _, codes = validate(spec, {"region": "XXX"})
    assert codes == ["E-INPUT-004"]


def test_error_lists_the_allowed_codes() -> None:
    _, errors = validate_inputs(
        plan_of("  region: {type: string, ref: regions}\n"), {"region": "X"}, CAT
    )
    assert "BIS" in errors[0].message and "OSC" in errors[0].message
    assert errors[0].input == "region"


def test_list_input_checks_every_item() -> None:
    spec = "  rs: {type: 'list<string>', ref: regions}\n"
    assert validate(spec, {"rs": ["BIS", "OSC"]})[1] == []
    assert validate(spec, {"rs": ["BIS", "NOPE"]})[1] == ["E-INPUT-004"]
    assert validate(spec, {"rs": "BIS"})[1] == ["E-INPUT-002"]


def test_required_default_and_optional() -> None:
    spec = "  a: {type: int}\n  b: {type: int, default: 5}\n  c: {type: int, required: false}\n"
    assert validate(spec, {}) == ({"b": 5}, ["E-INPUT-001"])
    assert validate(spec, {"a": 1}) == ({"a": 1, "b": 5}, [])
    assert validate(spec, {"a": 1, "b": 2, "c": 3}) == ({"a": 1, "b": 2, "c": 3}, [])


def test_unknown_input_is_an_error() -> None:
    assert validate("  a: {type: int}\n", {"a": 1, "zzz": 2})[1] == ["E-INPUT-005"]


@pytest.mark.parametrize(
    ("type_", "good", "bad"),
    [
        ("string", "x", 1),
        ("int", 3, "3"),
        ("int", 3, True),
        ("int", 3, 3.5),
        ("float", 2.5, "2.5"),
        ("float", 2, False),
        ("bool", True, 1),
        ("date", "2025-03-31", "31.03.2025"),
        ("date", "2024-02-29", "2025-02-29"),
        ("'list<int>'", [1, 2], [1, "2"]),
        ("'list<list<int>>'", [[1], [2, 3]], [[1], ["x"]]),
    ],
)
def test_types(type_: str, good: object, bad: object) -> None:
    spec = f"  v: {{type: {type_}}}\n"
    assert validate(spec, {"v": good})[1] == []
    assert validate(spec, {"v": bad})[1] == ["E-INPUT-002"]


def test_pattern() -> None:
    spec = "  q: {type: string, pattern: '^20[0-9]{2}Q[1-4]$'}\n"
    assert validate(spec, {"q": "2025Q3"})[1] == []
    assert validate(spec, {"q": "2025Q5"})[1] == ["E-INPUT-003"]
    assert validate(spec, {"q": "2025Q3 "})[1] == ["E-INPUT-003"]  # the whole text must match


def test_errors_are_sorted_by_input_declaration_order() -> None:
    spec = "  a: {type: int}\n  b: {type: int}\n"
    _, errors = validate_inputs(plan_of(spec), {"b": "x"}, CAT)
    assert [(e.input, e.code) for e in errors] == [("a", "E-INPUT-001"), ("b", "E-INPUT-002")]


def test_default_is_checked_too() -> None:
    spec = "  region: {type: string, ref: regions, default: NOPE}\n"
    assert validate(spec, {})[1] == ["E-INPUT-004"]


def test_validation_does_not_change_the_given_values() -> None:
    values: dict[str, object] = {"rs": ["BIS"]}
    validate_inputs(
        plan_of("  rs: {type: 'list<string>', ref: regions}\n  n: {type: int, default: 1}\n"),
        values,
        CAT,
    )
    assert values == {"rs": ["BIS"]}
