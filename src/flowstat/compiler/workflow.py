"""Typed view of a workflow document (phase 0 input), built from a tree that passed the shape check.

Expressions and templates are parsed here, so their syntax errors are reported with positions in
the original file. A quoted YAML string starts one column to the right of its quote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from flowstat.dsl.ast import Expr
from flowstat.dsl.parser import parse_expression
from flowstat.dsl.template import split_template
from flowstat.dsl.tree import Mapping, Scalar, Sequence, YNode
from flowstat.model.diagnostics import Diagnostic, Severity

_NEED = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:\[([A-Za-z_][A-Za-z0-9_]*|\*)\])?")
_FOREACH = re.compile(r"([A-Za-z_][A-Za-z0-9_]*) in catalogue\.([A-Za-z_][A-Za-z0-9_]*)")


@dataclass(frozen=True)
class Pos:
    line: int
    column: int


@dataclass(frozen=True)
class NeedRef:
    step: str
    selector: str | None  # None, "*" or a loop variable name
    pos: Pos


@dataclass(frozen=True)
class Foreach:
    var: str
    list_name: str
    pos: Pos


@dataclass(frozen=True)
class StepDecl:
    name: str
    pos: Pos
    action: str
    action_pos: Pos
    needs: tuple[NeedRef, ...]
    join_kind: str
    join_k: int
    join_k_pos: Pos | None
    when: Expr | None
    ensures: Expr | None
    foreach: Foreach | None
    with_: tuple[tuple[str, tuple[object, ...]], ...]
    on_failure: str
    retry: tuple[int, str, float] | None
    compensate: str | None
    compensate_pos: Pos | None = None


@dataclass(frozen=True)
class InputDecl:
    name: str
    pos: Pos
    type: str
    type_pos: Pos
    pattern: str | None
    pattern_pos: Pos | None
    ref: str | None
    ref_pos: Pos | None
    has_default: bool
    default: object
    required: bool


@dataclass(frozen=True)
class Workflow:
    name: str
    version: int
    inputs: tuple[InputDecl, ...]
    input_names: frozenset[str]
    steps: tuple[StepDecl, ...]
    goal_kind: str
    goals: tuple[tuple[str, Pos], ...]


def content_pos(node: Scalar) -> Pos:
    return Pos(node.line, node.column + (1 if node.quoted else 0))


def _scalar(node: YNode | None) -> Scalar | None:
    return node if isinstance(node, Scalar) else None


def _text(node: YNode | None) -> str:
    scalar = _scalar(node)
    return "" if scalar is None else str(scalar.value)


def _plain(node: YNode) -> object:
    if isinstance(node, Scalar):
        return node.value
    if isinstance(node, Sequence):
        return [_plain(i) for i in node.items]
    return {str(e.key.value): _plain(e.value) for e in node.entries}


def _build_step(name: Scalar, body: Mapping, diags: list[Diagnostic]) -> StepDecl:
    action = _scalar(body.get("action"))
    needs: list[NeedRef] = []
    needs_node = body.get("needs")
    if isinstance(needs_node, Sequence):
        for item in needs_node.items:
            if not isinstance(item, Scalar) or not isinstance(item.value, str):
                continue
            pos = content_pos(item)
            match = _NEED.fullmatch(item.value)
            if match is None:
                diags.append(
                    Diagnostic(
                        "E-REF-002",
                        Severity.ERROR,
                        f"'{item.value}' is not a step reference; use 'a', 'a[var]' or 'a[*]'",
                        pos.line,
                        pos.column,
                    )
                )
            else:
                needs.append(NeedRef(match.group(1), match.group(2), pos))

    join_kind, join_k, join_k_pos = "all_success", 0, None
    join_node = body.get("join")
    if isinstance(join_node, Scalar):
        join_kind = str(join_node.value)
    elif isinstance(join_node, Mapping):
        k_node = _scalar(join_node.get("at_least"))
        if k_node is not None and isinstance(k_node.value, int):
            join_kind, join_k = "at_least", k_node.value
            join_k_pos = Pos(k_node.line, k_node.column)

    def expression(key: str) -> Expr | None:
        node = _scalar(body.get(key))
        if node is None or not isinstance(node.value, str):
            return None
        pos = content_pos(node)
        tree, expr_diags = parse_expression(node.value, pos.line, pos.column)
        diags.extend(expr_diags)
        return tree

    foreach: Foreach | None = None
    foreach_node = _scalar(body.get("foreach"))
    if foreach_node is not None and isinstance(foreach_node.value, str):
        match = _FOREACH.fullmatch(foreach_node.value)
        if match is not None:
            foreach = Foreach(match.group(1), match.group(2), content_pos(foreach_node))

    with_: list[tuple[str, tuple[object, ...]]] = []
    with_node = body.get("with")
    if isinstance(with_node, Mapping):
        for entry in with_node.entries:
            value = entry.value
            if isinstance(value, Scalar) and isinstance(value.value, str):
                pos = content_pos(value)
                parts, template_diags = split_template(value.value, pos.line, pos.column)
                diags.extend(template_diags)
                with_.append((str(entry.key.value), tuple(parts)))
            else:
                with_.append((str(entry.key.value), (_plain(value),)))

    retry: tuple[int, str, float] | None = None
    retry_node = body.get("retry")
    if isinstance(retry_node, Mapping):
        max_node = _scalar(retry_node.get("max"))
        if max_node is not None and isinstance(max_node.value, int):
            delay_node = _scalar(retry_node.get("delay"))
            delay = 1.0
            if delay_node is not None and isinstance(delay_node.value, int | float):
                delay = float(delay_node.value)
            retry = (max_node.value, _text(retry_node.get("backoff")) or "fixed", delay)

    assert action is not None
    return StepDecl(
        name=str(name.value),
        pos=Pos(name.line, name.column),
        action=str(action.value),
        action_pos=content_pos(action),
        needs=tuple(needs),
        join_kind=join_kind,
        join_k=join_k,
        join_k_pos=join_k_pos,
        when=expression("when"),
        ensures=expression("ensures"),
        foreach=foreach,
        with_=tuple(with_),
        on_failure=_text(body.get("on_failure")) or "fail",
        retry=retry,
        compensate=_text(body.get("compensate")) or None,
        compensate_pos=(
            content_pos(compensate_node)
            if (compensate_node := _scalar(body.get("compensate")))
            else None
        ),
    )


def _build_input(name: Scalar, body: Mapping, diags: list[Diagnostic]) -> InputDecl:
    type_node = _scalar(body.get("type"))
    pattern = _scalar(body.get("pattern"))
    ref = _scalar(body.get("ref"))
    default = body.get("default")
    required = _scalar(body.get("required"))
    pattern_text = None if pattern is None else str(pattern.value)
    if pattern is not None and pattern_text is not None:
        try:
            re.compile(pattern_text)
        except re.error as error:
            pos = content_pos(pattern)
            diags.append(
                Diagnostic(
                    "E-SHAPE-004",
                    Severity.ERROR,
                    f"the pattern is not a valid regular expression: {error}",
                    pos.line,
                    pos.column,
                )
            )
            pattern_text = None
    has_default = default is not None
    return InputDecl(
        name=str(name.value),
        pos=Pos(name.line, name.column),
        type=_text(type_node),
        type_pos=content_pos(type_node) if type_node else Pos(name.line, name.column),
        pattern=pattern_text,
        pattern_pos=content_pos(pattern) if pattern else None,
        ref=None if ref is None else str(ref.value),
        ref_pos=content_pos(ref) if ref else None,
        has_default=has_default,
        default=_plain(default) if default is not None else None,
        required=not has_default and not (required is not None and required.value is False),
    )


def build_workflow(root: Mapping) -> tuple[Workflow, list[Diagnostic]]:
    """Build the typed view. The tree must have passed the shape check."""
    diags: list[Diagnostic] = []
    steps_node = root.get("steps")
    steps: list[StepDecl] = []
    if isinstance(steps_node, Mapping):
        for entry in steps_node.entries:
            if isinstance(entry.value, Mapping):
                steps.append(_build_step(entry.key, entry.value, diags))
    inputs_node = root.get("inputs")
    inputs: list[InputDecl] = []
    if isinstance(inputs_node, Mapping):
        for entry in inputs_node.entries:
            if isinstance(entry.value, Mapping):
                inputs.append(_build_input(entry.key, entry.value, diags))
    input_names = frozenset(i.name for i in inputs)
    goals_node = root.get("goals")
    goal_kind, goals = "any_of", []
    if isinstance(goals_node, Mapping):
        for entry in goals_node.entries:
            if isinstance(entry.value, Sequence):
                goal_kind = str(entry.key.value)
                goals = [
                    (str(g.value), content_pos(g))
                    for g in entry.value.items
                    if isinstance(g, Scalar)
                ]
    version = _scalar(root.get("version"))
    return (
        Workflow(
            name=_text(root.get("workflow")),
            version=int(version.value) if version and isinstance(version.value, int) else 1,
            inputs=tuple(inputs),
            input_names=input_names,
            steps=tuple(steps),
            goal_kind=goal_kind,
            goals=tuple(goals),
        ),
        diags,
    )
