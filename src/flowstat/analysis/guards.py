"""Analysis of branches (US-20, C6): guards that overlap and cases that nothing handles.

A branch group is a set of at least two steps with a ``when``, the same ``needs`` and guards over
the same variables, that are not instances of one ``foreach`` step (dsl-spec 8.1). For a group the
analysis asks the solver two questions: can two guards hold at once, and is there a case that no
guard holds. A warning is given only when the solver has a witness; when a guard is outside the
fragment, or the case analysis is too large, a note says so and nothing is claimed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from flowstat.analysis.signatures import Signatures
from flowstat.compiler.plan import InputSpec, Plan, PlanStep
from flowstat.dsl.ast import Ref, to_source
from flowstat.model.diagnostics import Diagnostic, Severity
from flowstat.solver.domains import Domain, as_number
from flowstat.solver.lower import Var, lower
from flowstat.solver.predicate import Pred, has_opaque, variables
from flowstat.solver.solve import Status, check, overlap, uncovered

_INPUT_KINDS = {"string": "str", "int": "int", "float": "real", "bool": "bool"}
_OUTPUT_KINDS = frozenset({"int", "real", "bool", "str"})


@dataclass(frozen=True, slots=True)
class _Guard:
    step: PlanStep
    pred: Pred
    clean: bool  # the whole guard is inside the fragment

    @property
    def line(self) -> int:
        assert self.step.when is not None
        return self.step.when.line

    @property
    def column(self) -> int:
        assert self.step.when is not None
        return self.step.when.column


class _Resolver:
    """Tells the lowering which references are variables, and what is known about them."""

    def __init__(self, plan: Plan, signatures: Signatures) -> None:
        self.plan = plan
        self.signatures = signatures
        self.domains: dict[str, Domain] = {}

    def __call__(self, ref: Ref) -> Var | None:
        if len(ref.path) != 2 or any(p.kind != "field" for p in ref.path):
            return None
        head, name = (str(p.value) for p in ref.path)
        domain = self._input(name) if head == "inputs" else self._output(head, name)
        if domain is None:
            return None
        variable = f"{head}.{name}"
        self.domains[variable] = domain
        return Var(variable, domain)

    def _input(self, name: str) -> Domain | None:
        for spec in self.plan.inputs:
            if spec.name == name:
                kind = _INPUT_KINDS.get(spec.type)
                return None if kind is None else _declared(kind, spec)
        return None

    def _output(self, step_id: str, name: str) -> Domain | None:
        try:
            step = self.plan.step(step_id)
        except KeyError:
            return None
        signature = self.signatures.get(step.action)
        if signature is None:
            return None
        choice = signature.choices.get(name)
        if choice is not None:
            values = _listed(step, choice.argument) or choice.default
            return Domain.enum(*values)
        kind = signature.fields.get(name)
        return Domain(kind) if kind in _OUTPUT_KINDS else None


def _declared(kind: str, spec: InputSpec) -> Domain:
    """The domain of an input: its type, narrowed by ``values``, ``min`` and ``max``."""
    low, high = as_number(spec.minimum), as_number(spec.maximum)
    return Domain(kind, spec.values, low, high)


def _listed(step: PlanStep, argument: str) -> tuple[str, ...]:
    """The strings of a list given as an argument of the step, or nothing."""
    for key, parts in step.with_:
        if key == argument and len(parts) == 1 and isinstance(parts[0], list):
            items = parts[0]
            if items and all(isinstance(i, str) for i in items):
                return tuple(items)
    return ()


def analyse(plan: Plan, signatures: Signatures) -> list[Diagnostic]:
    """Warnings and notes about the guards of a compiled plan, in the order of the plan."""
    resolver = _Resolver(plan, signatures)
    groups: dict[tuple[frozenset[str], frozenset[str]], list[_Guard]] = {}
    out: list[Diagnostic] = []
    for step in plan.steps:
        if step.when is None or step.loop is not None:
            continue
        pred = lower(step.when, resolver)
        guard = _Guard(step, pred, not has_opaque(pred))
        if check(pred, resolver.domains).status is Status.UNSAT:
            out.append(_dead(guard))
        groups.setdefault((frozenset(step.needs), frozenset(variables(pred))), []).append(guard)
    for members in groups.values():
        if len(members) >= 2:
            out.extend(_analyse_group(members, resolver.domains))
    return sorted(out, key=lambda d: (d.line, d.column))


def _dead(guard: _Guard) -> Diagnostic:
    """The guard is unsatisfiable (an opaque part cannot make an unsatisfiable rest satisfiable)."""
    assert guard.step.when is not None
    message = (
        f"'{guard.step.id}' can never run: {to_source(guard.step.when)} is false for every value "
        "that the inputs and the actions allow"
    )
    return _diagnostic("W-DEAD-001", Severity.WARNING, message, guard)


def _analyse_group(members: list[_Guard], domains: dict[str, Domain]) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    clean = [m for m in members if m.clean]
    undecided = False
    for i, a in enumerate(clean):
        for b in clean[i + 1 :]:
            result = overlap(a.pred, b.pred, domains)
            if result.status is Status.SAT:
                case = _describe(result.witness)
                message = f"'{a.step.id}' and '{b.step.id}' can both run, for example when {case}"
                out.append(_diagnostic("W-BRANCH-001", Severity.WARNING, message, a))
            elif result.status is Status.UNKNOWN:
                undecided = True
    first = members[0]
    names = ", ".join(f"'{m.step.id}'" for m in members)
    if len(clean) == len(members):
        gap = uncovered([m.pred for m in members], domains)
        if gap.status is Status.SAT:
            message = f"none of {names} runs when {_describe(gap.witness)}"
            out.append(_diagnostic("W-BRANCH-002", Severity.WARNING, message, first))
        undecided = undecided or gap.status is Status.UNKNOWN
        if undecided:
            message = f"the guards of {names} were not fully analysed: too many cases"
            out.append(_diagnostic("I-BRANCH-003", Severity.INFO, message, first))
    else:
        outside = ", ".join(f"'{m.step.id}'" for m in members if not m.clean)
        message = (
            f"the guards of {names} were not fully analysed: {outside} uses an expression "
            "outside what the analysis understands"
        )
        out.append(_diagnostic("I-BRANCH-003", Severity.INFO, message, first))
    return out


def _diagnostic(code: str, severity: Severity, message: str, guard: _Guard) -> Diagnostic:
    return Diagnostic(code, severity, message, guard.line, guard.column)


# -- wording ----------------------------------------------------------------------------------
def _describe(witness: dict[str, object] | None) -> str:
    assert witness is not None
    return ", ".join(f"{name} = {_value(v)}" for name, v in sorted(witness.items()))


def _value(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return f"'{v}'"
    if isinstance(v, Fraction):
        return _decimal(v)
    return str(v)


def _decimal(f: Fraction) -> str:
    """An exact decimal when there is one (1/2 is 0.5), else the fraction."""
    if f.denominator == 1:
        return str(f.numerator)
    rest = f.denominator
    for factor in (2, 5):
        while rest % factor == 0:
            rest //= factor
    if rest == 1:
        return format(Decimal(f.numerator) / Decimal(f.denominator), "f")
    return f"{f.numerator}/{f.denominator}"
