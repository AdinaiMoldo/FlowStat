"""Golden tests of the document shape validator (C1, level 1).

Every case is a small workflow with markers ``⟦`` at the places where an error is expected. The test
removes the markers, computes line and column of each one, and compares them with the diagnostics.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flowstat.dsl.reader import read_yaml
from flowstat.dsl.shape import check_shape
from flowstat.model.diagnostics import Diagnostic
from tests.unit.markers import MARK, split_markers

HEAD = "workflow: demo\nversion: 1\n"
STEPS = "steps:\n  a:\n    action: xlsx.read\n"
GOALS = "goals: {any_of: [a]}\n"
STEP_HEAD = HEAD + "steps:\n  a:\n    action: x.y\n"


def step(line: str) -> str:
    """A document whose only step has an extra line."""
    return STEP_HEAD + line + "\n" + GOALS


def run(text: str) -> tuple[list[Diagnostic], list[tuple[int, int]]]:
    clean, expected = split_markers(text)
    tree, read_diags = read_yaml(clean)
    assert read_diags == [] and tree is not None, [d.message for d in read_diags]
    return check_shape(tree), expected


VALID = HEAD + STEPS + GOALS

CASES: list[tuple[str, str, list[str]]] = [
    ("unknown top key", HEAD + MARK + "stepz: 1\n" + STEPS + GOALS, ["E-SHAPE-001"]),
    ("missing workflow", MARK + "version: 1\n" + STEPS + GOALS, ["E-SHAPE-002"]),
    ("missing steps", MARK + "workflow: demo\nversion: 1\n" + GOALS, ["E-SHAPE-002"]),
    ("version zero", "workflow: demo\nversion: " + MARK + "0\n" + STEPS + GOALS, ["E-SHAPE-004"]),
    ("version word", "workflow: demo\nversion: " + MARK + "one\n" + STEPS + GOALS, ["E-SHAPE-003"]),
    ("version float", "workflow: demo\nversion: " + MARK + "1.5\n" + STEPS + GOALS, ["E-SHAPE-003"]),
    ("version bool", "workflow: demo\nversion: " + MARK + "true\n" + STEPS + GOALS, ["E-SHAPE-003"]),
    ("workflow bad name", "workflow: " + MARK + "'bad name'\nversion: 1\n" + STEPS + GOALS, ["E-SHAPE-004"]),
    ("steps is a list", HEAD + "steps: " + MARK + "[]\n" + GOALS, ["E-SHAPE-003"]),
    ("steps empty", HEAD + "steps: " + MARK + "{}\n" + GOALS, ["E-SHAPE-004"]),
    ("step name invalid", HEAD + "steps:\n  " + MARK + "1bad:\n    action: x.y\n" + GOALS, ["E-SHAPE-004"]),
    ("step without action", HEAD + "steps:\n  a:\n    " + MARK + "with: {x: 1}\n" + GOALS, ["E-SHAPE-002"]),
    ("step key typo", step("    " + MARK + "wen: '1'"), ["E-SHAPE-001"]),
    ("action without package", HEAD + "steps:\n  a:\n    action: " + MARK + "nodot\n" + GOALS, ["E-SHAPE-004"]),
    ("needs scalar", step("    needs: " + MARK + "a"), ["E-SHAPE-003"]),
    ("needs item int", step("    needs: [" + MARK + "1]"), ["E-SHAPE-003"]),
    ("join bad word", step("    join: " + MARK + "sometimes"), ["E-SHAPE-004"]),
    ("join at_least zero", step("    join: {at_least: " + MARK + "0}"), ["E-SHAPE-004"]),
    ("join extra key", step("    join: {at_least: 2, " + MARK + "foo: 1}"), ["E-SHAPE-001"]),
    ("on_failure bad", step("    on_failure: " + MARK + "ignore"), ["E-SHAPE-004"]),
    ("retry without max", step("    retry: " + MARK + "{backoff: fixed}"), ["E-SHAPE-002"]),
    ("retry negative", step("    retry: {max: " + MARK + "-1}"), ["E-SHAPE-004"]),
    ("foreach malformed", step("    foreach: " + MARK + "region"), ["E-SHAPE-004"]),
    ("when is a number", step("    when: " + MARK + "5"), ["E-SHAPE-003"]),
    ("ensures is a list", step("    ensures: " + MARK + "[x]"), ["E-SHAPE-003"]),
    ("with is a list", step("    with: " + MARK + "[a]"), ["E-SHAPE-003"]),
    ("compensate is a number", step("    compensate: " + MARK + "5"), ["E-SHAPE-003"]),
    ("goals empty map", HEAD + STEPS + "goals: " + MARK + "{}\n", ["E-SHAPE-005"]),
    ("goals both kinds", HEAD + STEPS + "goals: " + MARK + "{any_of: [a], all_of: [a]}\n", ["E-SHAPE-005"]),
    ("goals empty list", HEAD + STEPS + "goals: {any_of: " + MARK + "[]}\n", ["E-SHAPE-004"]),
    ("input type typo", HEAD + "inputs:\n  q: {type: " + MARK + "strng}\n" + STEPS + GOALS, ["E-SHAPE-004"]),
    ("input without type", HEAD + "inputs:\n  q: " + MARK + "{pattern: x}\n" + STEPS + GOALS, ["E-SHAPE-002"]),
    ("input is a number", HEAD + "inputs:\n  q: " + MARK + "5\n" + STEPS + GOALS, ["E-SHAPE-003"]),
    ("input required not bool", HEAD + "inputs:\n  q: {type: string, required: " + MARK + "maybe}\n" + STEPS + GOALS, ["E-SHAPE-003"]),
    ("document is a list", MARK + "- a\n", ["E-SHAPE-003"]),
    ("unknown key without a neighbour", HEAD + MARK + "zzzzzz: 1\n" + STEPS + GOALS, ["E-SHAPE-001"]),
    (
        "two errors in one run",
        "workflow: demo\nversion: " + MARK + "0\n" + STEPS + "    on_failure: " + MARK + "ignore\n" + GOALS,
        ["E-SHAPE-004", "E-SHAPE-004"],
    ),
]  # fmt: skip


def test_valid_document_has_no_diagnostics() -> None:
    diags, _ = run(VALID)
    assert diags == []


def test_the_example_of_the_proposal_is_valid() -> None:
    path = Path(__file__).resolve().parents[3] / "examples" / "quarterly_consolidation.yaml"
    tree, read_diags = read_yaml(path.read_text(encoding="utf8"))
    assert read_diags == [] and tree is not None
    assert check_shape(tree) == []


def test_there_are_at_least_thirty_invalid_cases() -> None:
    assert len(CASES) >= 30


@pytest.mark.parametrize(("name", "text", "codes"), CASES, ids=[c[0] for c in CASES])
def test_invalid_document(name: str, text: str, codes: list[str]) -> None:
    diags, expected = run(text)
    assert [(d.code, d.line, d.column) for d in diags] == [
        (code, line, col) for code, (line, col) in zip(codes, expected, strict=True)
    ], [d.message for d in diags]


def messages(text: str) -> list[str]:
    diags, _ = run(text)
    return [d.message for d in diags]


def test_did_you_mean_for_a_top_level_key() -> None:
    assert "did you mean 'steps'" in messages(HEAD + "stepz: 1\n" + STEPS + GOALS)[0]


def test_did_you_mean_for_a_step_key() -> None:
    assert "did you mean 'when'" in messages(step("    wen: '1'"))[0]


def test_did_you_mean_is_case_insensitive() -> None:
    assert "did you mean 'when'" in messages(step("    When: '1'"))[0]


def test_did_you_mean_for_a_type() -> None:
    text = HEAD + "inputs:\n  q: {type: strng}\n" + STEPS + GOALS
    assert "did you mean 'string'" in messages(text)[0]


def test_no_suggestion_when_nothing_is_close() -> None:
    assert "did you mean" not in messages(HEAD + "zzzzzz: 1\n" + STEPS + GOALS)[0]


def test_the_allowed_values_are_listed() -> None:
    assert "all_success" in messages(step("    join: sometimes"))[0]


def test_a_missing_key_is_named() -> None:
    assert "'workflow'" in messages("version: 1\n" + STEPS + GOALS)[0]


def test_a_list_where_a_string_or_a_mapping_is_allowed() -> None:
    assert "a string or a mapping" in messages(step("    join: [a]"))[0]


def test_an_empty_value_is_named() -> None:
    assert "an empty value" in messages(step("    when:"))[0]


def test_a_number_is_named() -> None:
    assert "a number" in messages(step("    needs: 2.5"))[0]
