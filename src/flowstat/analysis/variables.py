"""What a guard reads: the variables of a plan, with their domains (C6).

``lower_guards`` turns the ``when`` of every step into a predicate (``solver/lower.py``) and
records the domain of each variable it meets: an input has its declared type, narrowed by
``values``, ``min`` and ``max``; an output field has the type its action declares; a choice (the
decision of an approval) is an enumeration. A reference that is not described is opaque.
"""

from __future__ import annotations

from dataclasses import dataclass

from flowstat.analysis.signatures import Signatures
from flowstat.compiler.plan import InputSpec, Plan, PlanStep
from flowstat.dsl.ast import Ref
from flowstat.solver.domains import Domain, as_number
from flowstat.solver.lower import Var, lower
from flowstat.solver.predicate import Pred

_INPUT_KINDS = {"string": "str", "int": "int", "float": "real", "bool": "bool"}
_OUTPUT_KINDS = frozenset({"int", "real", "bool", "str"})


class Resolver:
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


@dataclass(frozen=True, slots=True)
class Guards:
    preds: dict[str, Pred]  # step id -> its lowered guard, for every step that has a ``when``
    domains: dict[str, Domain]  # every variable that a guard reads


def lower_guards(plan: Plan, signatures: Signatures) -> Guards:
    resolver = Resolver(plan, signatures)
    preds = {s.id: lower(s.when, resolver) for s in plan.steps if s.when is not None}
    return Guards(preds, resolver.domains)
