"""Template splitter (C1, level 1): text with ``${path}`` references, used for values in ``with``.

A template is split into plain text and references. A value that is exactly one reference keeps the
type of the referenced output; otherwise the parts are joined as text at run time.
"""

from __future__ import annotations

from flowstat.dsl.ast import Ref
from flowstat.dsl.parser import parse_expression
from flowstat.model.diagnostics import Diagnostic, Severity

TemplatePart = str | Ref


def _position(text: str, offset: int, line: int, column: int) -> tuple[int, int]:
    before = text[:offset]
    newlines = before.count("\n")
    if newlines == 0:
        return line, column + offset
    return line + newlines, offset - before.rfind("\n")


def split_template(
    text: str, line: int = 1, column: int = 1
) -> tuple[list[TemplatePart], list[Diagnostic]]:
    parts: list[TemplatePart] = []
    diags: list[Diagnostic] = []
    pos = 0
    while pos < len(text):
        start = text.find("${", pos)
        if start < 0:
            parts.append(text[pos:])
            break
        if start > pos:
            parts.append(text[pos:start])
        ref_line, ref_col = _position(text, start, line, column)
        end = text.find("}", start + 2)
        if end < 0:
            diags.append(
                Diagnostic(
                    "E-EXPR-002",
                    Severity.ERROR,
                    "unterminated reference: '${' is never closed",
                    ref_line,
                    ref_col,
                )
            )
            break
        expr, ref_diags = parse_expression(text[start : end + 1], ref_line, ref_col)
        diags.extend(ref_diags)
        if isinstance(expr, Ref) and not ref_diags:
            parts.append(expr)
        pos = end + 1
    return parts, diags
