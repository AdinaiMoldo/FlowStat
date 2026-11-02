"""Tests of the YAML reader (B1): text to a tree with line and column positions."""

from __future__ import annotations

from flowstat.dsl.reader import read_yaml
from flowstat.dsl.tree import Mapping, Scalar, Sequence, YNode


def ok(text: str) -> YNode:
    node, diags = read_yaml(text)
    assert diags == [], [d.message for d in diags]
    assert node is not None
    return node


def test_mapping_with_positions() -> None:
    node = ok("workflow: demo\nversion: 4\n")
    assert isinstance(node, Mapping)
    keys = [e.key.value for e in node.entries]
    assert keys == ["workflow", "version"]
    first, second = node.entries
    assert (first.key.line, first.key.column) == (1, 1)
    assert (first.value.line, first.value.column) == (1, 11)
    assert (second.key.line, second.key.column) == (2, 1)
    assert isinstance(second.value, Scalar) and second.value.value == 4


def test_scalar_types_are_plain_python() -> None:
    node = ok("a: 1\nb: 2.5\nc: true\nd: null\ne: text\nf: '12'\ng: ~\n")
    assert isinstance(node, Mapping)
    values = {e.key.value: e.value.value for e in node.entries if isinstance(e.value, Scalar)}
    assert values == {"a": 1, "b": 2.5, "c": True, "d": None, "e": "text", "f": "12", "g": None}
    assert type(values["a"]) is int and type(values["b"]) is float and type(values["f"]) is str


def test_nested_mapping_and_sequence_positions() -> None:
    node = ok("steps:\n  load:\n    needs: [a, b]\n  other:\n    - x\n    - y: 1\n")
    assert isinstance(node, Mapping)
    steps = node.entries[0].value
    assert isinstance(steps, Mapping)
    assert (steps.line, steps.column) == (2, 3)
    load = steps.entries[0].value
    assert isinstance(load, Mapping)
    needs = load.entries[0].value
    assert isinstance(needs, Sequence)
    assert [(i.line, i.column) for i in needs.items] == [(3, 13), (3, 16)]
    other = steps.entries[1].value
    assert isinstance(other, Sequence)
    assert (other.items[0].line, other.items[0].column) == (5, 7)
    inner = other.items[1]
    assert isinstance(inner, Mapping)
    assert (inner.entries[0].key.line, inner.entries[0].key.column) == (6, 7)


def test_flow_mapping() -> None:
    node = ok("input: {type: string, pattern: '^a$'}\n")
    assert isinstance(node, Mapping)
    flow = node.entries[0].value
    assert isinstance(flow, Mapping)
    assert [e.key.value for e in flow.entries] == ["type", "pattern"]
    assert (flow.entries[0].key.line, flow.entries[0].key.column) == (1, 9)


def test_key_order_is_preserved() -> None:
    node = ok("z: 1\na: 2\nm: 3\n")
    assert isinstance(node, Mapping)
    assert [e.key.value for e in node.entries] == ["z", "a", "m"]


def test_comments_are_ignored() -> None:
    node = ok("# header\na: 1  # trailing\n")
    assert isinstance(node, Mapping) and len(node.entries) == 1


def test_syntax_error_has_a_position_and_no_tree() -> None:
    node, diags = read_yaml("a: [1, 2\nb: 3\n")
    assert node is None
    assert [d.code for d in diags] == ["E-YAML-001"]
    assert diags[0].line >= 1 and diags[0].column >= 1


def test_tab_indentation_is_an_error() -> None:
    node, diags = read_yaml("a:\n\tb: 1\n")
    assert node is None
    assert [d.code for d in diags] == ["E-YAML-001"]
    assert diags[0].line == 2


def test_duplicate_key_is_reported_at_the_second_key() -> None:
    node, diags = read_yaml("a: 1\nb: 2\na: 3\n")
    assert node is None
    assert [d.code for d in diags] == ["E-YAML-002"]
    assert (diags[0].line, diags[0].column) == (3, 1)


def test_empty_document() -> None:
    node, diags = read_yaml("# only a comment\n")
    assert node is None
    assert [d.code for d in diags] == ["E-YAML-003"]


def test_two_documents_are_an_error() -> None:
    node, diags = read_yaml("a: 1\n---\nb: 2\n")
    assert node is None
    assert [d.code for d in diags] == ["E-YAML-001"]


def test_unsupported_yaml_features_are_rejected() -> None:
    node, diags = read_yaml("a: &x 1\nb: *x\n")
    assert node is None
    assert [d.code for d in diags] == ["E-YAML-004"]


def test_scalar_document() -> None:
    node = ok("42\n")
    assert isinstance(node, Scalar) and node.value == 42


def test_mapping_lookup_by_key() -> None:
    node = ok("a: 1\nb: 2\n")
    assert isinstance(node, Mapping)
    found = node.get("b")
    assert isinstance(found, Scalar) and found.value == 2
    assert node.get("c") is None


def test_quoted_scalars_are_marked_so_that_expression_columns_can_be_computed() -> None:
    node = ok("a: \"x == 1\"\nb: 'y'\nc: plain\n")
    assert isinstance(node, Mapping)
    flags = {e.key.value: e.value.quoted for e in node.entries if isinstance(e.value, Scalar)}
    assert flags == {"a": True, "b": True, "c": False}
