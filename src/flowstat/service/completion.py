"""Completion inside ``${`` (US-27): only what can be read at the cursor.

The cursor is in a step; what that step may read is what C2 guarantees for it
(``analysis.availability.available_at``) plus the inputs. A text that is being typed rarely
compiles, so the unfinished reference is replaced by ``true`` before the text is compiled, and a
text that still does not compile gets no suggestions and a note that says why: nothing is guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from flowstat.analysis.availability import available_at
from flowstat.analysis.signatures import Signatures
from flowstat.compiler.catalogue import Catalogue
from flowstat.compiler.compile import compile_workflow
from flowstat.compiler.plan import Plan
from flowstat.model.diagnostics import Severity

NOT_INSIDE = "the cursor is not inside ${ ... }, so there is nothing to complete"
PLACEHOLDER = "true"  # stands for the reference that is being typed; it is a valid value anywhere


@dataclass(frozen=True, slots=True)
class Inside:
    start: int  # index in the line of the first character after "${"
    end: int | None  # index of the closing brace, if the line has one after the cursor
    prefix: str  # what is typed between "${" and the cursor


@dataclass(frozen=True, slots=True)
class Suggestion:
    label: str  # the text of the reference, without "${" and "}"
    kind: str  # input or output
    detail: str


@dataclass(frozen=True, slots=True)
class Completion:
    items: tuple[Suggestion, ...]
    prefix: str
    from_column: int  # the column (from 1) where the typed reference starts
    note: str | None  # why there are no items, when that is not obvious
    closed: bool = True  # the line already holds the closing brace; when not, the client adds it


def reference_at(line: str, column: int) -> Inside | None:
    """The reference the cursor (a column from 1) is inside of, or None."""
    cursor = column - 1
    if not 0 <= cursor <= len(line):
        return None
    opening = line.rfind("${", 0, cursor)
    if opening < 0 or "}" in line[opening + 2 : cursor]:
        return None
    # The brace after the cursor closes this reference only when what lies between is the rest of
    # a path; a quote or a space means it belongs to the line around it (`{rows: "${"}`).
    closing = line.find("}", cursor)
    if closing >= 0 and not re.fullmatch(r"[\w.\[\]*-]*", line[cursor:closing]):
        closing = -1
    return Inside(opening + 2, closing if closing >= 0 else None, line[opening + 2 : cursor])


def _neutral(lines: list[str], row: int, inside: Inside, cursor: int) -> str:
    """The text with the reference under the cursor replaced by a placeholder."""
    text = lines[row]
    stop = inside.end + 1 if inside.end is not None else cursor
    patched = [
        *lines[:row],
        text[: inside.start - 2] + PLACEHOLDER + text[stop:],
        *lines[row + 1 :],
    ]
    return "\n".join(patched)


def _step_at(plan: Plan, lines: list[str], line: int) -> str | None:
    """The step of the source text the line belongs to, or None outside the steps."""
    starts = {s.base: s.line for s in plan.steps}
    before = [(start, base) for base, start in starts.items() if start <= line]
    if not before:
        return None
    start, base = max(before)
    for number in range(start + 1, len(lines) + 1):  # the lines after the step's own line
        text = lines[number - 1]
        if text and not text[0].isspace() and text[0] != "#":  # the next top-level key
            return None if line >= number else base
    return base


def _fields(signatures: Signatures, action: str) -> dict[str, str]:
    signature = signatures.get(action)
    if signature is None:
        return {}
    if signature.outputs is not None:
        return dict(signature.outputs)
    return dict(signature.fields)


def _inputs(plan: Plan) -> list[Suggestion]:
    return [
        Suggestion(
            f"inputs.{i.name}",
            "input",
            f"input, {i.type}" + ("" if i.required else ", optional"),
        )
        for i in plan.inputs
    ]


def _outputs(plan: Plan, signatures: Signatures, base: str, without_when: bool) -> list[Suggestion]:
    if without_when:  # the condition of the step is being typed: do not assume it
        plan = replace(
            plan,
            steps=tuple(replace(s, when=None) if s.base == base else s for s in plan.steps),
        )
    here = plan.instances_of(base)[0]
    current = plan.step(here)
    guaranteed = available_at(plan, signatures, here)
    ancestors = plan.ancestors(here)
    out: list[Suggestion] = []
    for other in dict.fromkeys(s.base for s in plan.steps):
        members = plan.instances_of(other)
        first = plan.step(members[0])
        heads: list[tuple[str, bool]] = []  # (head, is a list)
        if first.loop is None:
            if other in guaranteed:
                heads.append((other, False))
        else:
            if all(m in ancestors for m in members):
                heads.append((f"{other}[*]", True))  # the instances that succeeded, a list
            if current.loop is not None and first.loop[0] == current.loop[0]:
                same = [
                    m for m in members if plan.step(m).loop == (current.loop[0], current.loop[1])
                ]
                if same and same[0] in guaranteed:
                    heads.append((f"{other}[{current.loop[0]}]", False))
        fields = _fields(signatures, first.action)
        for head, is_list in heads:
            if not fields:
                out.append(Suggestion(head, "output", f"output of {other} (no fields declared)"))
            for name, type_ in fields.items():
                shown = f"list<{type_}>" if is_list else type_
                out.append(Suggestion(f"{head}.{name}", "output", f"{shown}, from {other}"))
    return out


def complete(
    text: str,
    line: int,
    column: int,
    catalogue: Catalogue,
    known_actions: frozenset[str] | None,
    signatures: Signatures,
) -> Completion:
    """What can be written at (line, column) of ``text``, both from 1."""
    lines = text.split("\n")
    if not 1 <= line <= len(lines):
        return Completion((), "", column, NOT_INSIDE)
    inside = reference_at(lines[line - 1], column)
    if inside is None:
        return Completion((), "", column, NOT_INSIDE)
    from_column = inside.start + 1
    neutral = _neutral(lines, line - 1, inside, column - 1)
    result = compile_workflow(neutral, catalogue, known_actions)
    if result.plan is None:
        first = next((d for d in result.diagnostics if d.severity is Severity.ERROR), None)
        why = (
            f"the text has an error ({first.code} at {first.line}:{first.column}: {first.message})"
            if first
            else "the text does not compile"
        )
        return Completion((), inside.prefix, from_column, f"{why}, so nothing is suggested")
    base = _step_at(result.plan, neutral.split("\n"), line)
    items = _inputs(result.plan)
    if base is not None:
        typing_when = re.search(r"\bwhen\s*:", lines[line - 1]) is not None
        items += _outputs(result.plan, signatures, base, typing_when)
    return Completion(tuple(items), inside.prefix, from_column, None, inside.end is not None)
