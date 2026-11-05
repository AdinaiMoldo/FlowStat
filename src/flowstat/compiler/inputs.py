"""Validation of the values given for the inputs of a workflow (A4, US-02).

An input of type ``string`` or ``list<string>`` with ``ref: <list>`` accepts only the codes of that
catalogue list. The values are checked when a run is started, against the catalogue snapshot of the
published version, so a later change of the live catalogue cannot affect a version.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

from flowstat.compiler.catalogue import Catalogue
from flowstat.compiler.plan import InputSpec, Plan


@dataclass(frozen=True)
class InputError:
    code: str  # E-INPUT-001 .. E-INPUT-008
    input: str
    message: str


def _type_error(value: object, type_: str) -> str | None:
    """Return a description of the problem, or None when the value has the type."""
    if type_.startswith("list<") and type_.endswith(">"):
        if not isinstance(value, list):
            return "a list is expected"
        inner = type_[5:-1]
        for i, item in enumerate(value):
            problem = _type_error(item, inner)
            if problem is not None:
                return f"item {i}: {problem}"
        return None
    if type_ == "string":
        ok = isinstance(value, str)
    elif type_ == "int":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif type_ == "float":
        ok = isinstance(value, int | float) and not isinstance(value, bool)
    elif type_ == "bool":
        ok = isinstance(value, bool)
    elif type_ == "date":
        ok = isinstance(value, str) and _is_date(value)
    else:
        ok = False
    return None if ok else f"a value of type {type_} is expected"


def _is_date(text: str) -> bool:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) is None:
        return False
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _check(spec: InputSpec, value: object, catalogue: Catalogue) -> InputError | None:
    problem = _type_error(value, spec.type)
    if problem is not None:
        return InputError("E-INPUT-002", spec.name, f"'{spec.name}': {problem}")
    texts: list[str] = []
    if isinstance(value, str):
        texts = [value]
    elif isinstance(value, list):
        texts = [v for v in value if isinstance(v, str)]
    if (
        spec.pattern is not None
        and spec.type == "string"
        and re.fullmatch(spec.pattern, texts[0]) is None
    ):
        return InputError(
            "E-INPUT-003", spec.name, f"'{spec.name}': '{value}' does not match {spec.pattern}"
        )
    if spec.ref is not None:
        allowed = [item["code"] for item in catalogue.lists.get(spec.ref, ())]
        for text in texts:
            if text not in allowed:
                return InputError(
                    "E-INPUT-004",
                    spec.name,
                    f"'{spec.name}': '{text}' is not in the catalogue list '{spec.ref}' "
                    f"(allowed: {', '.join(allowed)})",
                )
    return _check_constraints(spec, value)


def _check_constraints(spec: InputSpec, value: object) -> InputError | None:
    """``values``, ``min`` and ``max`` of the declaration (dsl-spec 8.2)."""
    if spec.values is not None and value not in spec.values:
        listed = ", ".join(repr(v) for v in spec.values)
        return InputError(
            "E-INPUT-006", spec.name, f"'{spec.name}': {value!r} is not one of {listed}"
        )
    if isinstance(value, int | float) and not isinstance(value, bool):
        if spec.minimum is not None and value < spec.minimum:
            return InputError(
                "E-INPUT-007",
                spec.name,
                f"'{spec.name}': {value} is below the minimum {spec.minimum}",
            )
        if spec.maximum is not None and value > spec.maximum:
            return InputError(
                "E-INPUT-008",
                spec.name,
                f"'{spec.name}': {value} is above the maximum {spec.maximum}",
            )
    return None


def validate_inputs(
    plan: Plan, values: dict[str, object], catalogue: Catalogue
) -> tuple[dict[str, object], list[InputError]]:
    """Return the values with defaults applied, and the errors sorted by input declaration."""
    errors: list[InputError] = []
    resolved: dict[str, object] = {}
    known = {spec.name for spec in plan.inputs}
    for spec in plan.inputs:
        if spec.name in values:
            value = values[spec.name]
        elif spec.has_default:
            value = spec.default
        elif spec.required:
            errors.append(InputError("E-INPUT-001", spec.name, f"'{spec.name}' is required"))
            continue
        else:
            continue
        error = _check(spec, value, catalogue)
        if error is not None:
            errors.append(error)
        else:
            resolved[spec.name] = value
    for name in sorted(set(values) - known):
        errors.append(InputError("E-INPUT-005", name, f"'{name}' is not an input of the workflow"))
    return resolved, errors
