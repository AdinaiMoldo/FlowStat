"""B1: read DSL text (YAML) into a tree with line and column positions.

The only module of the compiler packages that imports a third-party library (``ruamel.yaml``,
see ``tests/architecture/rules.py``). Everything else in the DSL is our own code.
"""

from __future__ import annotations

from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarstring import DoubleQuotedScalarString, SingleQuotedScalarString

from flowstat.dsl.tree import Entry, Mapping, Scalar, Sequence, YNode
from flowstat.model.diagnostics import Diagnostic, Severity


class _Unsupported(Exception):
    def __init__(self, message: str, line: int, column: int) -> None:
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column


def _error(code: str, message: str, line: int, column: int) -> Diagnostic:
    return Diagnostic(code, Severity.ERROR, message, line, column)


def _plain(value: object) -> object:
    """Strip the library's scalar subclasses: callers see only str, int, float, bool or None."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    return str(value)


def _convert(value: Any, line: int, column: int) -> YNode:
    lc = getattr(value, "lc", None)
    if isinstance(value, dict) and lc is not None:
        entries: list[Entry] = []
        for key in value:
            k_line, k_col = lc.key(key)
            v_line, v_col = lc.value(key)
            entries.append(
                Entry(
                    Scalar(k_line + 1, k_col + 1, _plain(key)),
                    _convert(value[key], v_line + 1, v_col + 1),
                )
            )
        return Mapping(line, column, tuple(entries))
    if isinstance(value, list) and lc is not None:
        items: list[YNode] = []
        for index, item in enumerate(value):
            i_line, i_col = lc.item(index)
            items.append(_convert(item, i_line + 1, i_col + 1))
        return Sequence(line, column, tuple(items))
    anchor = getattr(value, "anchor", None)
    if anchor is not None and getattr(anchor, "value", None) is not None:
        raise _Unsupported("YAML anchors and aliases are not supported", line, column)
    quoted = isinstance(value, SingleQuotedScalarString | DoubleQuotedScalarString)
    return Scalar(line, column, _plain(value), quoted)


def read_yaml(text: str) -> tuple[YNode | None, list[Diagnostic]]:
    """Read ``text``. Returns the tree, or None together with the diagnostics of the problem."""
    yaml = YAML(typ="rt")
    yaml.allow_duplicate_keys = False
    yaml.preserve_quotes = True  # tells quoted text from plain text, for expression columns
    try:
        data = yaml.load(text)
    except YAMLError as exc:
        mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
        if getattr(exc, "problem", None) == "could not find expected ':'":
            # the library notices a missing colon on the next line; the key starts earlier
            mark = getattr(exc, "context_mark", None) or mark
        line = mark.line + 1 if mark is not None else 1
        column = mark.column + 1 if mark is not None else 1
        if type(exc).__name__ == "DuplicateKeyError":
            # the library reports the first occurrence in the context and the second as the problem
            return None, [_error("E-YAML-002", "duplicate key", line, column)]
        problem = getattr(exc, "problem", None) or "invalid YAML"
        return None, [_error("E-YAML-001", str(problem), line, column)]
    if data is None:
        return None, [_error("E-YAML-003", "the document is empty", 1, 1)]
    try:
        return _convert(data, 1, 1), []
    except _Unsupported as exc:
        return None, [_error("E-YAML-004", exc.message, exc.line, exc.column)]
