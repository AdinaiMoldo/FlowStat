"""Tests of the template splitter: text with ${...} references (values inside `with`)."""

from __future__ import annotations

from flowstat.dsl.ast import Ref
from flowstat.dsl.template import split_template


def parts_of(text: str) -> list[object]:
    parts, diags = split_template(text)
    assert diags == [], [d.message for d in diags]
    return [
        p if isinstance(p, str) else "${" + ".".join(str(x.value) for x in p.path) + "}"
        for p in parts
    ]


def test_plain_text_is_one_part() -> None:
    assert parts_of("report.xlsx") == ["report.xlsx"]


def test_empty_text_has_no_parts() -> None:
    assert parts_of("") == []


def test_text_and_references_alternate() -> None:
    assert parts_of("${region.code}/${inputs.quarter}.xlsx") == [
        "${region.code}", "/", "${inputs.quarter}", ".xlsx",
    ]  # fmt: skip


def test_a_single_reference_keeps_its_type() -> None:
    parts, _ = split_template("${load[region].rows}")
    assert len(parts) == 1 and isinstance(parts[0], Ref)


def test_reference_positions_point_into_the_original_file() -> None:
    parts, _ = split_template("ab${x.y}", line=4, column=10)
    ref = parts[1]
    assert isinstance(ref, Ref)
    assert (ref.line, ref.column) == (4, 12)


def test_positions_follow_newlines() -> None:
    parts, _ = split_template("a\nb ${x}", line=1, column=1)
    ref = parts[-1]
    assert isinstance(ref, Ref)
    assert (ref.line, ref.column) == (2, 3)


def test_unterminated_reference() -> None:
    _, diags = split_template("a ${x.y", line=3, column=5)
    assert [d.code for d in diags] == ["E-EXPR-002"]
    assert (diags[0].line, diags[0].column) == (3, 7)


def test_empty_reference() -> None:
    _, diags = split_template("a ${} b")
    assert [d.code for d in diags] == ["E-EXPR-001"]


def test_invalid_path_inside_a_reference() -> None:
    _, diags = split_template("${a..b}")
    assert [d.code for d in diags] == ["E-EXPR-001"]


def test_a_dollar_without_brace_is_text() -> None:
    assert parts_of("cost $5 and $$") == ["cost $5 and $$"]


def test_several_errors_are_all_reported() -> None:
    _, diags = split_template("${} and ${a..b}")
    assert len(diags) == 2
