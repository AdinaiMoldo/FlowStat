"""Document shape validator (C1, level 1): a table-driven check of the YAML tree.

The shape of a workflow document is data (``WORKFLOW_SHAPE`` at the end of the file), and one
small interpreter walks the tree against it. Nothing here knows about the keys of the DSL, so a new
key is one new line in the table. Every problem becomes a diagnostic with the position of the
offending node; unknown keys get a did-you-mean suggestion (edit distance at most 2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from flowstat.dsl.suggest import suggest
from flowstat.dsl.tree import Mapping, Scalar, Sequence, YNode
from flowstat.model.diagnostics import Diagnostic, Severity

# -- the shape language ----------------------------------------------------------------------


@dataclass(frozen=True)
class Str:
    pattern: str | None = None
    hint: str = ""  # what the value should look like, shown when the pattern fails
    suggestions: tuple[str, ...] = ()  # words for did-you-mean when the pattern fails


@dataclass(frozen=True)
class Int:
    minimum: int | None = None


@dataclass(frozen=True)
class Positive:
    """A number (integer or decimal) greater than zero."""


@dataclass(frozen=True)
class Number:
    """Any number, integer or decimal, of either sign."""


@dataclass(frozen=True)
class Bool:
    pass


@dataclass(frozen=True)
class Enum:
    values: tuple[str, ...]


@dataclass(frozen=True)
class AnyValue:
    pass


@dataclass(frozen=True)
class Seq:
    item: Shape
    min_items: int = 0


@dataclass(frozen=True)
class Key:
    shape: Shape
    required: bool = False


@dataclass(frozen=True)
class Map:
    keys: dict[str, Key] = field(default_factory=dict)
    extra: Shape | None = None  # shape of values under any other key (open mappings)
    name_pattern: str | None = None  # pattern for those other keys
    min_entries: int = 0
    exactly_one_of: tuple[str, ...] = ()


@dataclass(frozen=True)
class ByKind:
    """Different shapes for a scalar, a mapping and a sequence in the same place."""

    scalar: Shape | None = None
    mapping: Shape | None = None
    sequence: Shape | None = None


Shape = Str | Int | Positive | Number | Bool | Enum | AnyValue | Seq | Map | ByKind

# -- the interpreter ---------------------------------------------------------------------------


def _kind(node: YNode) -> str:
    if isinstance(node, Mapping):
        return "a mapping"
    if isinstance(node, Sequence):
        return "a list"
    value = node.value
    if value is None:
        return "an empty value"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, int):
        return "an integer"
    if isinstance(value, float):
        return "a number"
    return "a string"


def _quoted(words: tuple[str, ...]) -> str:
    return ", ".join(f"'{w}'" for w in words)


class _Checker:
    def __init__(self) -> None:
        self.diags: list[Diagnostic] = []

    def report(self, code: str, message: str, line: int, column: int) -> None:
        self.diags.append(Diagnostic(code, Severity.ERROR, message, line, column))

    def wrong_kind(self, node: YNode, expected: str) -> None:
        self.report(
            "E-SHAPE-003", f"expected {expected}, found {_kind(node)}", node.line, node.column
        )

    def invalid(self, node: YNode, message: str, hint: str | None = None) -> None:
        text = message if hint is None else f"{message}; did you mean '{hint}'?"
        self.report("E-SHAPE-004", text, node.line, node.column)

    def check(self, node: YNode, shape: Shape) -> None:
        if isinstance(shape, AnyValue):
            return
        if isinstance(shape, ByKind):
            self._by_kind(node, shape)
        elif isinstance(shape, Map):
            self._map(node, shape)
        elif isinstance(shape, Seq):
            self._seq(node, shape)
        else:
            self._scalar(node, shape)

    def _by_kind(self, node: YNode, shape: ByKind) -> None:
        chosen = (
            shape.mapping
            if isinstance(node, Mapping)
            else shape.sequence
            if isinstance(node, Sequence)
            else shape.scalar
        )
        if chosen is None:
            allowed = [
                name
                for name, s in (
                    ("a string", shape.scalar),
                    ("a mapping", shape.mapping),
                    ("a list", shape.sequence),
                )
                if s is not None
            ]
            self.wrong_kind(node, " or ".join(allowed))
        else:
            self.check(node, chosen)

    def _scalar(self, node: YNode, shape: Str | Int | Positive | Number | Bool | Enum) -> None:
        if not isinstance(node, Scalar):
            expected = {
                Str: "a string",
                Int: "an integer",
                Positive: "a number",
                Number: "a number",
                Bool: "a boolean",
                Enum: "a string",
            }
            self.wrong_kind(node, expected[type(shape)])
            return
        value = node.value
        if isinstance(shape, Bool):
            if not isinstance(value, bool):
                self.wrong_kind(node, "a boolean")
        elif isinstance(shape, Number):
            if isinstance(value, bool) or not isinstance(value, int | float):
                self.wrong_kind(node, "a number")
        elif isinstance(shape, Positive):
            if isinstance(value, bool) or not isinstance(value, int | float):
                self.wrong_kind(node, "a number")
            elif value <= 0:
                self.invalid(node, "the value must be greater than 0")
        elif isinstance(shape, Int):
            if isinstance(value, bool) or not isinstance(value, int):
                self.wrong_kind(node, "an integer")
            elif shape.minimum is not None and value < shape.minimum:
                self.invalid(node, f"the value must be at least {shape.minimum}")
        elif not isinstance(value, str):
            self.wrong_kind(node, "a string")
        elif isinstance(shape, Enum):
            if value not in shape.values:
                self.invalid(
                    node,
                    f"invalid value '{value}'; allowed: {_quoted(shape.values)}",
                    suggest(value, shape.values),
                )
        elif shape.pattern is not None and re.fullmatch(shape.pattern, value) is None:
            what = f"; expected {shape.hint}" if shape.hint else ""
            self.invalid(node, f"invalid value '{value}'{what}", suggest(value, shape.suggestions))

    def _seq(self, node: YNode, shape: Seq) -> None:
        if not isinstance(node, Sequence):
            self.wrong_kind(node, "a list")
            return
        if len(node.items) < shape.min_items:
            self.invalid(node, f"the list needs at least {shape.min_items} item(s)")
        for item in node.items:
            self.check(item, shape.item)

    def _map(self, node: YNode, shape: Map) -> None:
        if not isinstance(node, Mapping):
            self.wrong_kind(node, "a mapping")
            return
        present = {str(e.key.value) for e in node.entries}
        for entry in node.entries:
            name = str(entry.key.value)
            known = shape.keys.get(name)
            if known is not None:
                self.check(entry.value, known.shape)
            elif shape.extra is not None:
                if shape.name_pattern and re.fullmatch(shape.name_pattern, name) is None:
                    self.report(
                        "E-SHAPE-004",
                        f"invalid name '{name}'; use letters, digits and '_', not a leading digit",
                        entry.key.line,
                        entry.key.column,
                    )
                self.check(entry.value, shape.extra)
            else:
                hint = suggest(name, shape.keys)
                message = f"unknown key '{name}'"
                if hint is not None:
                    message += f"; did you mean '{hint}'?"
                self.report("E-SHAPE-001", message, entry.key.line, entry.key.column)
        for name, key in shape.keys.items():
            if key.required and name not in present:
                self.report("E-SHAPE-002", f"missing required key '{name}'", node.line, node.column)
        if shape.exactly_one_of and sum(n in present for n in shape.exactly_one_of) != 1:
            self.report(
                "E-SHAPE-005",
                f"exactly one of {_quoted(shape.exactly_one_of)} is required",
                node.line,
                node.column,
            )
        if len(node.entries) < shape.min_entries:
            self.invalid(node, f"at least {shape.min_entries} entry is required")


def check_shape(root: YNode, shape: Shape | None = None) -> list[Diagnostic]:
    """Check a document tree against a shape (the workflow shape by default)."""
    checker = _Checker()
    checker.check(root, WORKFLOW_SHAPE if shape is None else shape)
    return sorted(checker.diags, key=lambda d: (d.line, d.column))


# -- the shape of a workflow document -------------------------------------------------------------

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_ACTION = rf"{_IDENT}\.{_IDENT}(\.{_IDENT})*"
_BASE_TYPES = ("string", "int", "float", "bool", "date")

INPUT_SHAPE = Map(
    {
        "type": Key(
            Str(
                rf"({'|'.join(_BASE_TYPES)}|list<.+>)",
                hint="string, int, float, bool, date or list<...>",
                suggestions=_BASE_TYPES,
            ),
            required=True,
        ),
        "pattern": Key(Str()),
        "ref": Key(Str(_IDENT, hint="the name of a catalogue list")),
        "default": Key(AnyValue()),
        "required": Key(Bool()),
        "values": Key(Seq(AnyValue(), min_items=1)),
        "min": Key(Number()),
        "max": Key(Number()),
    }
)

STEP_SHAPE = Map(
    {
        "action": Key(Str(_ACTION, hint="'package.name'"), required=True),
        "with": Key(Map(extra=AnyValue())),
        "needs": Key(Seq(Str())),
        "join": Key(
            ByKind(
                scalar=Enum(("all_success", "any_success", "all_done")),
                mapping=Map({"at_least": Key(Int(1), required=True)}),
            )
        ),
        "when": Key(Str()),
        "ensures": Key(Str()),
        "foreach": Key(
            Str(rf"{_IDENT} in catalogue\.{_IDENT}", hint="'<name> in catalogue.<list>'")
        ),
        "on_failure": Key(Enum(("fail", "continue", "compensate"))),
        "retry": Key(
            Map(
                {
                    "max": Key(Int(0), required=True),
                    "backoff": Key(Enum(("fixed", "exponential"))),
                    "delay": Key(Positive()),
                }
            )
        ),
        "compensate": Key(Str(_ACTION, hint="'package.name'")),
    }
)

WORKFLOW_SHAPE = Map(
    {
        "workflow": Key(Str(_IDENT, hint="letters, digits and '_'"), required=True),
        "version": Key(Int(1), required=True),
        "inputs": Key(Map(extra=INPUT_SHAPE, name_pattern=_IDENT)),
        "steps": Key(Map(extra=STEP_SHAPE, name_pattern=_IDENT, min_entries=1), required=True),
        "goals": Key(
            Map(
                {
                    "any_of": Key(Seq(Str(), min_items=1)),
                    "all_of": Key(Seq(Str(), min_items=1)),
                },
                exactly_one_of=("any_of", "all_of"),
            ),
            required=True,
        ),
    }
)
