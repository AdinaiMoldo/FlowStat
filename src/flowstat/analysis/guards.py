"""Analysis of branches (US-20, C6): guards that overlap and cases that nothing handles.

A branch group is a set of at least two steps with a ``when``, the same ``needs`` and guards over
the same variables, that are not instances of one ``foreach`` step (dsl-spec 8.1). For a group the
analysis asks the solver two questions: can two guards hold at once, and is there a case that no
guard holds. A warning is given only when the solver has a witness; when a guard is outside the
fragment, or the case analysis is too large, a note says so and nothing is claimed.
"""

from __future__ import annotations

from dataclasses import dataclass

from flowstat.analysis.signatures import Signatures
from flowstat.analysis.variables import lower_guards
from flowstat.analysis.wording import describe
from flowstat.compiler.plan import Plan, PlanStep
from flowstat.dsl.ast import to_source
from flowstat.model.diagnostics import Diagnostic, Severity
from flowstat.solver.domains import Domain
from flowstat.solver.predicate import Pred, has_opaque, variables
from flowstat.solver.solve import Status, check, overlap, uncovered


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


def analyse(plan: Plan, signatures: Signatures) -> list[Diagnostic]:
    """Warnings and notes about the guards of a compiled plan, in the order of the plan."""
    lowered = lower_guards(plan, signatures)
    groups: dict[tuple[frozenset[str], frozenset[str]], list[_Guard]] = {}
    out: list[Diagnostic] = []
    for step in plan.steps:
        if step.when is None or step.loop is not None:
            continue
        pred = lowered.preds[step.id]
        guard = _Guard(step, pred, not has_opaque(pred))
        if check(pred, lowered.domains).status is Status.UNSAT:
            out.append(_dead(guard))
        groups.setdefault((frozenset(step.needs), frozenset(variables(pred))), []).append(guard)
    for members in groups.values():
        if len(members) >= 2:
            out.extend(_analyse_group(members, lowered.domains))
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
                case = describe(result.witness)
                message = f"'{a.step.id}' and '{b.step.id}' can both run, for example when {case}"
                out.append(_diagnostic("W-BRANCH-001", Severity.WARNING, message, a))
            elif result.status is Status.UNKNOWN:
                undecided = True
    first = members[0]
    names = ", ".join(f"'{m.step.id}'" for m in members)
    if len(clean) == len(members):
        gap = uncovered([m.pred for m in members], domains)
        if gap.status is Status.SAT:
            message = f"none of {names} runs when {describe(gap.witness)}"
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
