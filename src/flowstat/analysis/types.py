"""Types of expressions and arguments (C1, phase 4): docs/dsl-spec.md, sections 4 and 9.

The check runs on a compiled plan, so every reference is already resolved to a step or an input.
It infers the type of every ``when``, ``ensures`` and argument of a step and reports where the run
would fail or a declared type is broken. A subexpression that is wrong has the type ``any``, which
fits everything, so one mistake gives one message. A foreach step is checked once, through its
first instance.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from flowstat.analysis.signatures import ActionSignature, Signatures
from flowstat.compiler.plan import InputSpec, Plan, PlanStep
from flowstat.dsl.ast import (
    Attr,
    Binary,
    Call,
    Compare,
    ErrorNode,
    Expr,
    Index,
    ListLit,
    Literal,
    Name,
    Node,
    PathPart,
    Ref,
    Unary,
)
from flowstat.model.diagnostics import Diagnostic, Severity
from flowstat.model.hosts import leading_host


# -- the types ---------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Prim:
    name: str  # string, int, float, bool, date or null


@dataclass(frozen=True, slots=True)
class ListT:
    item: Type


@dataclass(frozen=True, slots=True)
class RecordT:
    fields: tuple[tuple[str, Type], ...]

    def get(self, name: str) -> Type | None:
        return next((t for n, t in self.fields if n == name), None)


@dataclass(frozen=True, slots=True)
class AnyT:
    pass


Type = Prim | ListT | RecordT | AnyT

ANY = AnyT()
STRING, INT, FLOAT, BOOL, DATE, NULL = (
    Prim("string"),
    Prim("int"),
    Prim("float"),
    Prim("bool"),
    Prim("date"),
    Prim("null"),
)
ROW = RecordT((("code", STRING), ("name", STRING), ("value", FLOAT)))
_NAMES = {"string": STRING, "int": INT, "float": FLOAT, "bool": BOOL, "date": DATE}


def parse_type(text: str) -> Type:
    """A type in the notation of the DSL: ``int``, ``list<row>``, ``record{a: int}``, ``any``."""
    parsed, rest = _parse(text.strip())
    if rest.strip():
        raise ValueError(f"unexpected '{rest.strip()}' in the type '{text}'")
    return parsed


def _parse(text: str) -> tuple[Type, str]:
    text = text.lstrip()
    word = ""
    for ch in text:
        if not (ch.isalnum() or ch == "_"):
            break
        word += ch
    rest = text[len(word) :]
    if word in _NAMES:
        return _NAMES[word], rest
    if word == "null":
        return NULL, rest
    if word == "any":
        return ANY, rest
    if word == "row":
        return ROW, rest
    if word == "list" and rest.startswith("<"):
        item, after = _parse(rest[1:])
        after = after.lstrip()
        if not after.startswith(">"):
            raise ValueError(f"missing '>' in the type '{text}'")
        return ListT(item), after[1:]
    if word == "record" and rest.startswith("{"):
        fields: list[tuple[str, Type]] = []
        rest = rest[1:].lstrip()
        while not rest.startswith("}"):
            name, _, rest = rest.partition(":")
            kind, rest = _parse(rest)
            fields.append((name.strip(), kind))
            rest = rest.lstrip()
            if rest.startswith(","):
                rest = rest[1:].lstrip()
            elif not rest.startswith("}"):
                raise ValueError(f"missing '}}' in the type '{text}'")
        return RecordT(tuple(fields)), rest[1:]
    raise ValueError(f"unknown type '{text}'")


def show(t: Type) -> str:
    match t:
        case Prim(name=name):
            return name
        case ListT(item=item):
            return f"list<{show(item)}>"
        case RecordT(fields=fields):
            if t == ROW:
                return "row"
            return "record{" + ", ".join(f"{n}: {show(k)}" for n, k in fields) + "}"
        case AnyT():
            return "any"


def noun(t: Type) -> str:
    """A type as a person says it: "a list", "a number"."""
    match t:
        case Prim(name="string"):
            return "a string"
        case Prim(name="int" | "float"):
            return "a number"
        case Prim(name="bool"):
            return "a boolean"
        case Prim(name="date"):
            return "a date"
        case Prim():
            return "null"
        case ListT():
            return "a list"
        case RecordT():
            return "a record"
        case AnyT():
            return "a value"


def assignable(s: Type, t: Type) -> bool:
    """Whether a value of type ``s`` may be given where ``t`` is expected (subtyping, spec 4)."""
    if isinstance(s, AnyT) or isinstance(t, AnyT):
        return True
    if s == t:
        return True
    if s == INT and t == FLOAT:
        return True
    if isinstance(s, ListT) and isinstance(t, ListT):
        return assignable(s.item, t.item)
    if isinstance(s, RecordT) and isinstance(t, RecordT):
        return all(
            (have := s.get(name)) is not None and assignable(have, kind) for name, kind in t.fields
        )
    return False


def _number(t: Type) -> bool:
    return t in (INT, FLOAT)


def _scalar(t: Type) -> bool:
    return isinstance(t, AnyT) or (isinstance(t, Prim) and t != NULL)


def input_type(spec: InputSpec) -> Type:
    try:
        return parse_type(spec.type)
    except ValueError:
        return ANY


def plain_type(value: object) -> Type:
    """The type of a literal YAML value in ``with``."""
    if value is None:
        return NULL
    if isinstance(value, bool):
        return BOOL
    if isinstance(value, int):
        return INT
    if isinstance(value, float):
        return FLOAT
    if isinstance(value, str):
        return STRING
    if isinstance(value, list):
        return ListT(_join([plain_type(v) for v in value]))
    if isinstance(value, dict):
        return RecordT(tuple((str(k), plain_type(v)) for k, v in value.items()))
    return ANY


def _join(types: Sequence[Type]) -> Type:
    """One type for the items of a list literal: the first when all fit it, else ``any``."""
    if not types:
        return ANY
    if all(assignable(t, types[0]) for t in types):
        return types[0]
    return FLOAT if all(_number(t) for t in types) else ANY


# -- the check ---------------------------------------------------------------------------------
_BUILTINS = {"len", "sum", "min", "max", "count", "exists"}


class _Checker:
    def __init__(self, plan: Plan, signatures: Signatures) -> None:
        self.plan = plan
        self.signatures = signatures
        self.inputs = {i.name: input_type(i) for i in plan.inputs}
        self.by_id = {s.id: s for s in plan.steps}
        self.by_base: dict[str, PlanStep] = {}
        for s in plan.steps:
            self.by_base.setdefault(s.base, s)
        self.diags: list[Diagnostic] = []

    # -- reporting
    def report(self, code: str, severity: Severity, message: str, node: Node | PlanStep) -> None:
        self.diags.append(Diagnostic(code, severity, message, node.line, node.column))

    def error(self, message: str, node: Node | PlanStep, code: str = "E-TYPE-001") -> None:
        self.report(code, Severity.ERROR, message, node)

    # -- steps
    def run(self) -> list[Diagnostic]:
        seen: set[str] = set()
        for step in self.plan.steps:
            if step.base in seen:
                continue
            seen.add(step.base)
            self.check_step(step)
        unique: dict[tuple[str, int, int, str], Diagnostic] = {}
        for d in self.diags:
            unique.setdefault((d.code, d.line, d.column, d.message), d)
        return sorted(unique.values(), key=lambda d: (d.line, d.column, d.code))

    def check_step(self, step: PlanStep) -> None:
        for kind, expr in (("when", step.when), ("ensures", step.ensures)):
            if expr is None:
                continue
            t = self.infer(expr, step)
            if not isinstance(t, AnyT) and t != BOOL:
                self.error(f"'{kind}' must be true or false, not {noun(t)}", expr)
        sig = self.signatures.get(step.action)
        if step.timeout is not None and sig is not None and not sig.waits:
            self.error(
                f"the action {step.action} does not wait for a person, so the step has no "
                "time to limit; 'timeout' is for steps such as approval.wait",
                step,
                "E-TYPE-006",
            )
        given: set[str] = set()
        for name, parts in step.with_:
            given.add(name)
            if name == "url" and sig is not None and sig.hosts is not None:
                self.check_host(parts, sig.hosts, step)
            t = self.argument_type(parts, step)
            if sig is None:
                continue
            expected = self.expected(sig, name)
            if expected is None:
                self.report(
                    "W-TYPE-004",
                    Severity.WARNING,
                    f"the action {step.action} does not take the argument '{name}'",
                    step,
                )
            elif not assignable(t, expected):
                where: Node | PlanStep = step
                if len(parts) == 1 and isinstance(parts[0], Ref):
                    where = parts[0]
                self.error(
                    f"the argument '{name}' of {step.action} must be {show(expected)}, "
                    f"not {show(t)}",
                    where,
                )
        if sig is not None:
            for name in sig.params:
                if name not in given:
                    self.error(
                        f"the action {step.action} needs the argument '{name}'",
                        step,
                        "E-TYPE-003",
                    )

    def check_host(self, parts: tuple[object, ...], hosts: frozenset[str], step: PlanStep) -> None:
        """A constant url must name a host of the list (US-39). A host that a reference builds is
        decided when the call is made."""
        host = leading_host(parts)
        if host is not None and host not in hosts:
            self.error(
                f"{step.action} may not call the host '{host}': the hosts allowed here are "
                f"{', '.join(sorted(hosts))}",
                step,
                "E-TYPE-007",
            )

    def expected(self, sig: ActionSignature, name: str) -> Type | None:
        text = sig.params.get(name) or sig.optional.get(name)
        if text is None:
            return None
        try:
            return parse_type(text)
        except ValueError:
            return ANY

    def argument_type(self, parts: tuple[object, ...], step: PlanStep) -> Type:
        if len(parts) == 1 and isinstance(parts[0], Ref):
            return self.reference(parts[0], step)
        if len(parts) == 1 and not isinstance(parts[0], str | Ref):
            return plain_type(parts[0])
        for part in parts:  # a text with references
            if isinstance(part, Ref):
                t = self.reference(part, step)
                if not _scalar(t):
                    self.error(
                        f"{noun(t)} cannot be part of a text; use a reference on its own", part
                    )
        return STRING

    # -- references
    def reference(self, ref: Ref, step: PlanStep, quiet: bool = False) -> Type:
        parts = list(ref.path)
        head = str(parts[0].value)
        rest = parts[1:]
        if head == "inputs":
            if not rest:
                return ANY
            kind = self.inputs.get(str(rest[0].value), ANY)
            return self.walk(kind, rest[1:], ref, quiet)
        target = self.by_id.get(head) or self.by_base.get(head)
        if target is None:
            return ANY
        fan_in = bool(rest) and rest[0].kind == "star"
        if fan_in:
            rest = rest[1:]
        if not rest or rest[0].kind != "field":
            return ANY
        kind = self.output_field(target, str(rest[0].value), ref, quiet)
        result = self.walk(kind, rest[1:], ref, quiet)
        return ListT(result) if fan_in else result

    def output_field(self, step: PlanStep, name: str, ref: Ref, quiet: bool) -> Type:
        sig = self.signatures.get(step.action)
        if sig is None:
            return ANY
        if sig.outputs is not None and name in sig.outputs:
            return _lenient(sig.outputs[name])
        if name in sig.choices:
            return STRING
        if sig.outputs is None and not sig.fields:
            return ANY
        if not quiet:
            known = sorted(set(sig.outputs or ()) | set(sig.choices))
            listed = ", ".join(known) if known else "none"
            self.error(
                f"the action {step.action} has no output field '{name}'; it has: {listed}",
                ref,
                "E-TYPE-002",
            )
        return ANY

    def walk(self, t: Type, parts: Sequence[PathPart], ref: Ref, quiet: bool) -> Type:
        for part in parts:
            if isinstance(t, AnyT):
                return ANY
            if part.kind == "field":
                t = self.field_of(t, str(part.value), ref, quiet)
            elif part.kind == "index":
                t = t.item if isinstance(t, ListT) else ANY
            else:
                return ANY
        return t

    def field_of(self, t: Type, name: str, node: Node, quiet: bool) -> Type:
        if isinstance(t, AnyT):
            return ANY
        if isinstance(t, RecordT):
            found = t.get(name)
            if found is not None:
                return found
            if not quiet:
                self.error(f"the record has no field '{name}'", node, "E-TYPE-002")
            return ANY
        if not quiet:
            self.error(f"{noun(t)} has no field '{name}'", node, "E-TYPE-002")
        return ANY

    # -- expressions
    def infer(self, expr: Expr, step: PlanStep, quiet: bool = False) -> Type:
        match expr:
            case Literal(value=value):
                return plain_type(value)
            case ErrorNode() | Name():
                return ANY
            case Ref():
                return self.reference(expr, step, quiet)
            case ListLit(items=items):
                return ListT(_join([self.infer(i, step, quiet) for i in items]))
            case Unary(op=op, operand=operand):
                return self.unary(op, operand, step, quiet)
            case Binary(op=op, left=left, right=right):
                return self.binary(expr, op, left, right, step, quiet)
            case Compare(op=op, left=left, right=right):
                return self.compare(expr, op, left, right, step, quiet)
            case Attr(obj=obj, name=name):
                return self.field_of(self.infer(obj, step, quiet), name, expr, quiet)
            case Index(obj=obj, index=index):
                return self.index(expr, obj, index, step, quiet)
            case Call():
                return self.call(expr, step, quiet)
        return ANY  # pragma: no cover

    def unary(self, op: str, operand: Expr, step: PlanStep, quiet: bool) -> Type:
        t = self.infer(operand, step, quiet)
        if op == "not":
            if not isinstance(t, AnyT) and t != BOOL and not quiet:
                self.error(f"'not' needs true or false, not {noun(t)}", operand)
            return BOOL
        if isinstance(t, AnyT) or _number(t):
            return t
        if not quiet:
            self.error(f"'-' needs a number, not {noun(t)}", operand)
        return ANY

    def binary(
        self, node: Binary, op: str, left: Expr, right: Expr, step: PlanStep, quiet: bool
    ) -> Type:
        a, b = self.infer(left, step, quiet), self.infer(right, step, quiet)
        if op in ("and", "or"):
            for side, t in ((left, a), (right, b)):
                if not isinstance(t, AnyT) and t != BOOL and not quiet:
                    self.error(f"'{op}' needs true or false, not {noun(t)}", side)
            return BOOL
        if isinstance(a, AnyT) or isinstance(b, AnyT):
            return FLOAT if op == "/" else ANY
        if op == "+" and a == STRING and b == STRING:
            return STRING
        if _number(a) and _number(b):
            if op == "/":
                return FLOAT
            return INT if a == INT and b == INT else FLOAT
        if not quiet:
            needs = "two numbers or two strings" if op == "+" else "two numbers"
            self.error(f"'{op}' needs {needs}, not {noun(a)} and {noun(b)}", node)
        return ANY

    def compare(
        self, node: Compare, op: str, left: Expr, right: Expr, step: PlanStep, quiet: bool
    ) -> Type:
        a, b = self.infer(left, step, quiet), self.infer(right, step, quiet)
        if isinstance(a, AnyT) or isinstance(b, AnyT):
            return BOOL
        if op in ("==", "!="):
            if not quiet and not _comparable(a, b):
                value = "false" if op == "==" else "true"
                self.report(
                    "W-TYPE-005",
                    Severity.WARNING,
                    f"{noun(a)} is never equal to {noun(b)}, so '{op}' is always {value}",
                    node,
                )
            return BOOL
        if op == "in":
            self.membership(node, a, b, quiet)
            return BOOL
        ordered = (
            (_number(a) and _number(b))
            or (a == STRING and b == STRING)
            or (a == DATE and b == DATE)
        )
        if not ordered and not quiet:
            self.error(
                f"{noun(a).capitalize()} cannot be compared with {noun(b)}: "
                f"'{op}' takes two numbers, two strings or two dates",
                node,
            )
        return BOOL

    def membership(self, node: Compare, a: Type, b: Type, quiet: bool) -> None:
        if quiet:
            return
        if isinstance(b, ListT):
            return
        if b == STRING:
            if a != STRING:
                self.error(f"'in' with a string on the right needs a string, not {noun(a)}", node)
            return
        if isinstance(b, RecordT):
            if a != STRING:
                self.error(f"'in' with a record on the right needs a string, not {noun(a)}", node)
            return
        self.error(f"'in' needs a list, a string or a record on the right, not {noun(b)}", node)

    def index(self, node: Index, obj: Expr, index: Expr, step: PlanStep, quiet: bool) -> Type:
        t, i = self.infer(obj, step, quiet), self.infer(index, step, quiet)
        if isinstance(t, AnyT):
            return ANY
        if isinstance(t, ListT):
            if not isinstance(i, AnyT) and i != INT and not quiet:
                self.error(f"a list is indexed by a whole number, not {noun(i)}", index)
            return t.item
        if isinstance(t, RecordT):
            if not isinstance(i, AnyT) and i != STRING and not quiet:
                self.error(f"a record is indexed by a string, not {noun(i)}", index)
            return ANY
        if not quiet:
            self.error(f"{noun(t)} cannot be indexed", node)
        return ANY

    def call(self, node: Call, step: PlanStep, quiet: bool) -> Type:
        name = node.func.name if isinstance(node.func, Name) else None
        if name not in _BUILTINS:  # outside the language: valid, and unknown (spec 3)
            for arg in node.args:
                self.infer(arg, step, quiet)
            return ANY
        if name == "exists":
            for arg in node.args:
                self.infer(arg, step, True)  # a missing field is what exists() is for
            if len(node.args) != 1 and not quiet:
                self.error("'exists' takes one argument", node)
            return BOOL
        types = [self.infer(a, step, quiet) for a in node.args]
        if len(types) != 1:
            if not quiet:
                self.error(f"'{name}' takes one argument", node)
            return INT if name in ("len", "count") else ANY
        t = types[0]
        if isinstance(t, AnyT):
            return INT if name in ("len", "count") else ANY
        if name == "len":
            if not (isinstance(t, ListT | RecordT) or t == STRING) and not quiet:
                self.error(f"'len' needs a list, a string or a record, not {noun(t)}", node)
            return INT
        if name == "count":
            if not isinstance(t, ListT) and not quiet:
                self.error(f"'count' needs a list, not {noun(t)}", node)
            return INT
        item = t.item if isinstance(t, ListT) else None
        ok = item is not None and (
            isinstance(item, AnyT)
            or (name == "sum" and _number(item))
            or (name in ("min", "max") and (_number(item) or item == STRING))
        )
        if not ok and not quiet:
            what = "a list of numbers" if name == "sum" else "a list of numbers or of strings"
            self.error(f"'{name}' needs {what}, not {noun(t)}", node)
        return item if ok and item is not None else ANY


def _lenient(text: str) -> Type:
    try:
        return parse_type(text)
    except ValueError:
        return ANY


def _comparable(a: Type, b: Type) -> bool:
    """Whether two values of these types can ever be equal."""
    if a == NULL or b == NULL:
        return True
    if _number(a) and _number(b):
        return True
    if isinstance(a, ListT) and isinstance(b, ListT):
        return _comparable(a.item, b.item)
    if isinstance(a, RecordT) and isinstance(b, RecordT):
        return True
    return a == b


def check_types(plan: Plan, signatures: Mapping[str, ActionSignature]) -> list[Diagnostic]:
    """Errors and warnings about the types of a plan (docs/dsl-spec.md, section 9)."""
    return _Checker(plan, signatures).run()
