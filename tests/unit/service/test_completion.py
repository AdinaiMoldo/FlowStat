"""Completion inside ``${`` offers only what is available at the cursor (US-27)."""

from __future__ import annotations

import re

import pytest

from flowstat.actions.registry import default_registry
from flowstat.analysis import availability
from flowstat.compiler.catalogue import Catalogue
from flowstat.compiler.compile import compile_workflow
from flowstat.service.completion import Completion, complete, reference_at
from tests.unit.api.helpers import EXAMPLE, REGIONS

REGISTRY = default_registry()
CURSOR = "§"
CATALOGUE = Catalogue({"regions": tuple(REGIONS)})

W1 = """workflow: w
version: 1
inputs:
  mode: {type: string, values: [fast, slow]}
steps:
  a: {action: approval.wait, with: {role: x}}
  b: {action: approval.wait, needs: [a], when: "${a.decision} == 'approved'", with: {role: x}}
  c: {action: approval.wait, needs: [a], when: "${a.decision} == 'rejected'", with: {role: x}}
  d: {action: approval.wait, needs: [b], with: {role: x}}
  e: {action: approval.wait, needs: [b, c], join: any_success, with: {role: x}}
  f: {action: approval.wait, needs: [a], when: "${a.decision} == 'approved'", with: {role: x}}
goals: {any_of: [d, e, f]}
"""


def at(text: str, step: str, arguments: str) -> tuple[str, int, int]:
    """The text with ``arguments`` as the ``with`` of ``step`` (the cursor is the mark), the line
    and the column of the cursor."""
    start = re.search(rf"^  {re.escape(step)}:.*$", text, re.MULTILINE)
    assert start, step
    lines = text.split("\n")
    number = text[: start.start()].count("\n")
    if "{" in lines[number]:  # a step in flow style, on one line
        lines[number] = re.sub(r"with: \{[^}]*\}", f"with: {{{arguments}}}", lines[number])
        row = number
    else:  # a step in block style: its `with` line, or a new one after its action
        end = next(
            (i for i in range(number + 1, len(lines)) if lines[i] and not lines[i][0].isspace()),
            len(lines),
        )
        own = [i for i in range(number, end) if lines[i].lstrip().startswith("with:")]
        if own:
            row = own[0]
            lines[row] = f"    with: {{{arguments}}}"
        else:
            row = next(i for i in range(number, end) if "action:" in lines[i]) + 1
            lines.insert(row, f"    with: {{{arguments}}}")
    marked = "\n".join(lines)
    index = lines[row].index(CURSOR)
    return marked.replace(CURSOR, "", 1), row + 1, index + 1


def ask(
    text: str, step: str, arguments: str = f'x: "${{{CURSOR}}}"', source: str = ""
) -> Completion:
    body, line, column = at(source or text, step, arguments)
    return complete(body, line, column, CATALOGUE, REGISTRY.names(), REGISTRY.signatures())


def labels(result: Completion) -> list[str]:
    return [i.label for i in result.items]


# -- where the cursor is ------------------------------------------------------------------------
def cursor_in(line: str) -> tuple[str, int]:
    """The line without the mark, and the column (from 1) of the mark."""
    return line.replace(CURSOR, "", 1), line.index(CURSOR) + 1


@pytest.mark.parametrize(
    ("marked", "prefix"),
    [
        (f'with: {{x: "${{{CURSOR}}}"}}', ""),  # between { and }
        (f'with: {{x: "${{load[reg{CURSOR}"}}', "load[reg"),
        (f'with: {{x: "${{load[reg{CURSOR}ion].rows}}"}}', "load[reg"),  # in the middle of one
        (f'when: "${{a.d}} == 1 and ${{b{CURSOR}.x}}"', "b"),  # the second of two
        (f'when: "${{a{CURSOR}.d}} == 1 and ${{b.x}}"', "a"),  # the first of two
        (f"x: ${{{CURSOR}", ""),  # unfinished, at the end of the line
    ],
)
def test_the_cursor_inside_a_reference_gives_what_is_typed_so_far(marked: str, prefix: str) -> None:
    line, column = cursor_in(marked)
    found = reference_at(line, column)
    assert found is not None
    assert found.prefix == prefix
    assert line[found.start - 2 : found.start] == "${"


@pytest.mark.parametrize(
    "marked",
    [
        f'with: {{x: "${{a.b}}{CURSOR}"}}',  # after the reference
        f'with: {{x: "{CURSOR}${{a.b}}"}}',  # before the `${`
        f'with: {{x: "${CURSOR}{{a.b}}"}}',  # between the dollar sign and the brace
        f'with: {{x: "{CURSOR}$"}}',
        f"x: plain{CURSOR}",
        CURSOR,
    ],
)
def test_a_cursor_outside_a_reference_gives_nothing(marked: str) -> None:
    line, column = cursor_in(marked)
    assert reference_at(line, column) is None


@pytest.mark.parametrize(
    ("marked", "closes"),
    [
        (f'with: {{x: "${{{CURSOR}}}"}}', True),  # the editor closed the brace for us
        (f'with: {{x: "${{a{CURSOR}bc}}"}}', True),  # the rest of a path, then the brace
        (f'with: {{x: "${{{CURSOR}"}}', False),  # the brace is the one of the mapping
        (f"with: {{x: ${{{CURSOR} }}", False),  # a space: not part of a path
        (f"x: ${{{CURSOR}", False),
    ],
)
def test_the_brace_after_the_cursor_closes_the_reference_only_when_it_can(
    marked: str, closes: bool
) -> None:
    line, column = cursor_in(marked)
    found = reference_at(line, column)
    assert found is not None
    assert (found.end is not None) is closes


def test_an_unfinished_reference_in_a_mapping_is_replaced_without_taking_its_brace() -> None:
    marked = with_open_reference()
    result = complete(*marked, CATALOGUE, REGISTRY.names(), REGISTRY.signatures())
    assert result.note is None
    assert result.closed is False  # the client has to write the brace of the reference
    assert "consolidate.rows" in labels(result)


def with_open_reference() -> tuple[str, int, int]:
    """The example where the `with` of publish holds `rows: "${` and the line goes on."""
    lines = EXAMPLE.split("\n")
    row = next(i for i, x in enumerate(lines) if x.startswith('    with: {rows: "${consolidate'))
    lines[row] = '    with: {rows: "${"}'
    return "\n".join(lines), row + 1, lines[row].index("${") + 3


def test_a_column_past_the_end_of_the_line_gives_nothing() -> None:
    assert reference_at("x: ${a}", 99) is None
    assert reference_at("x: ${a}", 0) is None


# -- what is offered ----------------------------------------------------------------------------
def test_an_approval_after_the_consolidation_reads_it_the_lists_before_it_and_the_inputs() -> None:
    result = ask(EXAMPLE, "approve")
    assert result.note is None
    assert labels(result) == [
        "inputs.quarter",
        "load[*].rows",
        "load[*].count",
        "check[*].rows",
        "check[*].checked",
        "consolidate.rows",
        "consolidate.regions",
    ]
    assert [i.kind for i in result.items] == ["input"] + ["output"] * 6
    assert not any(i.label.startswith(("publish", "return", "approve")) for i in result.items)


def test_a_step_of_the_fan_out_reads_its_own_instance_of_the_step_before() -> None:
    result = ask(EXAMPLE, "check")
    assert labels(result) == ["inputs.quarter", "load[region].rows", "load[region].count"]


def test_the_consolidation_may_read_the_lists_of_the_fan_out_it_waits_for() -> None:
    # `check[*]` is the list of the instances that succeeded; the quorum does not matter for it
    result = ask(EXAMPLE, "consolidate")
    assert "check[*].rows" in labels(result)
    assert "load[*].rows" in labels(result)
    assert not [label for label in labels(result) if "[region]" in label]


def test_a_step_sees_only_the_steps_that_are_guaranteed_when_it_starts() -> None:
    # d waits for b, so b has succeeded; e waits for b or c, so neither is certain
    d = ask(W1, "d")
    e = ask(W1, "e")
    assert "b.decision" in labels(d) and "a.decision" in labels(d)
    assert [x for x in labels(e) if x.startswith(("b.", "c."))] == []
    assert "a.decision" in labels(e)


def test_a_step_that_is_not_an_ancestor_is_never_offered() -> None:
    assert [x for x in labels(ask(W1, "f")) if not x.startswith(("a.", "inputs."))] == []
    assert not any(x.startswith("d.") for x in labels(ask(W1, "e")))


def test_the_fields_come_from_the_declaration_of_the_action() -> None:
    assert [x for x in labels(ask(EXAMPLE, "publish")) if x.startswith("approve.")] == [
        "approve.decision",
        "approve.by",
    ]


def test_inputs_come_first_and_the_outputs_follow_the_order_of_the_plan() -> None:
    result = ask(EXAMPLE, "return_to_regions")
    assert labels(result)[0] == "inputs.quarter"
    heads = [x.split(".")[0] for x in labels(result)[1:]]
    assert heads.index("check[*]") < heads.index("consolidate") < heads.index("approve")


def test_the_condition_of_the_step_itself_is_not_assumed_while_it_is_being_typed() -> None:
    """In its own `when` the guard is not known yet, so what the guard would justify is left out
    until the condition is written; what is certain without it is offered."""
    lines = W1.split("\n")
    row = next(i for i, x in enumerate(lines) if x.startswith("  b:"))
    lines[row] = lines[row].replace(
        "when: \"${a.decision} == 'approved'\"", f'when: "${{{CURSOR}}}"'
    )
    column = lines[row].index(CURSOR) + 1
    text = "\n".join(lines).replace(CURSOR, "", 1)
    result = complete(text, row + 1, column, CATALOGUE, REGISTRY.names(), REGISTRY.signatures())
    assert result.note is None
    assert "a.decision" in labels(result)


# -- what is not offered, and why ---------------------------------------------------------------
def test_outside_a_reference_there_is_nothing_and_the_note_says_so() -> None:
    result = complete(EXAMPLE, 22, 3, CATALOGUE, REGISTRY.names(), REGISTRY.signatures())
    assert result.items == ()
    assert result.note is not None and "${" in result.note


def test_a_text_that_does_not_compile_offers_nothing_and_names_the_first_error() -> None:
    broken = EXAMPLE.replace("[publish, return_to_regions]", "[publish, return_to_region]")
    result = ask(broken, "approve", source=broken)
    assert result.items == ()
    assert result.note is not None
    assert "E-REF-001" in result.note and "38" in result.note


def test_outside_the_steps_only_the_inputs_are_offered() -> None:
    lines = EXAMPLE.split("\n")
    row = next(i for i, text in enumerate(lines) if text.startswith("goals:"))
    lines[row] = "goals: {any_of: [publish, return_to_regions]}  # ${"
    result = complete(
        "\n".join(lines),
        row + 1,
        len(lines[row]) + 1,
        CATALOGUE,
        REGISTRY.names(),
        REGISTRY.signatures(),
    )
    assert labels(result) == ["inputs.quarter"]


def test_the_range_to_replace_starts_where_the_reference_starts() -> None:
    result = ask(EXAMPLE, "approve", arguments=f'x: "${{cons{CURSOR}}}"')
    assert result.prefix == "cons"
    _, _, column = at(EXAMPLE, "approve", f'x: "${{cons{CURSOR}}}"')
    assert result.from_column == column - len("cons")


# -- the story, tested as written: a suggestion is used without a warning -----------------------
def probe(text: str, step: str, label: str) -> str:
    """The text with ``${label}`` read by ``step`` in an extra argument."""
    lines = text.split("\n")
    number = next(i for i, x in enumerate(lines) if re.match(rf"^  {re.escape(step)}:", x))
    reference = f'probe: "${{{label}}}"'
    end = next(
        (i for i in range(number + 1, len(lines)) if lines[i] and not lines[i][0].isspace()),
        len(lines),
    )
    for row in range(number, end):
        if "with: {" in lines[row]:
            lines[row] = lines[row].replace("with: {", f"with: {{{reference}, ", 1)
            return "\n".join(lines)
    row = next(i for i in range(number, end) if "action:" in lines[i])
    lines.insert(row + 1, f"    with: {{{reference}}}")
    return "\n".join(lines)


def warnings_of(text: str) -> tuple[list[str], list[str]]:
    result = compile_workflow(text, CATALOGUE, REGISTRY.names())
    errors = [d.code for d in result.diagnostics]
    if result.plan is None:
        return errors, []
    found = availability.analyse(result.plan, REGISTRY.signatures())
    return errors, [d.code for d in found if d.code.startswith("W-AVAIL")]


@pytest.mark.parametrize(("text", "at_least"), [(EXAMPLE, 25), (W1, 10)], ids=["example", "w1"])
def test_every_output_that_is_offered_can_be_read_without_an_error_or_a_warning(
    text: str, at_least: int
) -> None:
    plan = compile_workflow(text, CATALOGUE, REGISTRY.names()).plan
    assert plan is not None
    checked = 0
    for base in dict.fromkeys(s.base for s in plan.steps):
        marked, line, column = at(text, base, f'x: "${{{CURSOR}}}"')
        offered = complete(marked, line, column, CATALOGUE, REGISTRY.names(), REGISTRY.signatures())
        for item in offered.items:
            if item.kind != "output":
                continue
            after = warnings_of(probe(text, base, item.label))
            assert after == ([], []), (base, item.label, after)
            checked += 1
    assert checked >= at_least


def test_an_output_of_an_ancestor_that_is_not_offered_does_draw_a_warning() -> None:
    """The other direction, for plain steps: what the list leaves out is what the analysis warns
    about, so the list is neither too short nor too long."""
    plan = compile_workflow(W1, CATALOGUE, REGISTRY.names()).plan
    assert plan is not None
    left_out = 0
    for step in plan.steps:
        marked, line, column = at(W1, step.base, f'x: "${{{CURSOR}}}"')
        offered = labels(
            complete(marked, line, column, CATALOGUE, REGISTRY.names(), REGISTRY.signatures())
        )
        for ancestor in sorted(plan.ancestors(step.id)):
            label = f"{ancestor}.decision"
            if label in offered:
                continue
            errors, warns = warnings_of(probe(W1, step.base, label))
            assert errors == [] and "W-AVAIL-001" in warns, (step.id, label, errors, warns)
            left_out += 1
    assert left_out >= 2
