"""Re-typing of recorded outputs against the consumers of the new version (E-MIG-007).

A step that has not started yet will read what the steps before it produced. Those outputs are
recorded, so the new reads can be tried on the real values: a field the output does not have, or
a value of another type than the argument it goes to, would fail the run later; the check says
so now. Only references to the output of a step that succeeded are tried: a step that did not
run has no output, and that is the business of the guards, not of the types.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from flowstat.analysis.availability import references
from flowstat.analysis.signatures import Signatures
from flowstat.analysis.types import assignable, parse_type, plain_type, show
from flowstat.compiler.plan import PlanStep
from flowstat.dsl.ast import Ref, to_source
from flowstat.engine.evaluator import EvalContext, EvalError, evaluate
from flowstat.migration.reasons import OUTPUT_INCOMPATIBLE, Reason


def incompatibilities(
    steps: Sequence[PlanStep], ctx: EvalContext, signatures: Signatures
) -> list[Reason]:
    """The reads of ``steps`` (which have no history) that the recorded outputs cannot serve."""
    found: list[Reason] = []
    for step in steps:
        for ref, proven in _reads(step):
            head = str(ref.path[0].value)
            if head in proven or not _recorded(ref, ctx):
                continue
            try:
                evaluate(ref, ctx)
            except EvalError as error:
                if error.code == "E-RUN-003":  # the output has no such field
                    text = f"{step.id} reads {to_source(ref)}: {error.message}"
                    found.append(Reason(OUTPUT_INCOMPATIBLE, step.id, text))
        found.extend(_argument_types(step, ctx, signatures))
    return found


def _reads(step: PlanStep) -> Iterator[tuple[Ref, frozenset[str]]]:
    for _, parts in step.with_:
        for part in parts:
            if isinstance(part, Ref):
                yield part, frozenset()
    for expr in (step.when, step.ensures):
        if expr is not None:
            yield from references(expr)


def _recorded(ref: Ref, ctx: EvalContext) -> bool:
    """The reference points at a step that has a recorded output."""
    head = str(ref.path[0].value)
    if head == "inputs":
        return False
    if any(part.kind == "star" for part in ref.path[1:]):
        return any(i in ctx.outputs for i in ctx.instances.get(head, ()))
    return head in ctx.outputs


def _argument_types(step: PlanStep, ctx: EvalContext, signatures: Signatures) -> list[Reason]:
    """An argument that is exactly one reference gets the recorded value: is its type right?"""
    signature = signatures.get(step.action)
    if signature is None:
        return []
    found: list[Reason] = []
    for name, parts in step.with_:
        if len(parts) != 1 or not isinstance(parts[0], Ref) or not _recorded(parts[0], ctx):
            continue
        declared = signature.params.get(name) or signature.optional.get(name)
        if declared is None:
            continue
        try:
            expected = parse_type(declared)
            value = evaluate(parts[0], ctx)
        except (ValueError, EvalError):
            continue  # an unreadable type is not our finding; a missing field was reported above
        actual = plain_type(value)
        if not assignable(actual, expected):
            text = (
                f"{step.id} gives {to_source(parts[0])} to '{name}' of {step.action}: "
                f"the recorded value is {show(actual)}, the action takes {show(expected)}"
            )
            found.append(Reason(OUTPUT_INCOMPATIBLE, step.id, text))
    return found
