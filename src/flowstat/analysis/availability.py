"""Availability of outputs (C2): which steps are guaranteed to have succeeded when a step starts.

docs/dsl-spec.md, section 10. For every step ``Y`` and every ancestor ``X`` the analysis keeps a
predicate ``Av(Y, X)`` over the inputs and the outputs that guards read: whenever it holds and
``Y`` starts, ``X`` has SUCCEEDED. It is a sufficient condition, never a necessary one, so a step
that the analysis cannot place is reported (a warning that is not a promise), and nothing is
promised that a run can miss. A reference to the output of ``X`` in ``Y`` is valid when the guard of
``Y`` entails ``Av(Y, X)``; the solver of C6 decides that, with a value that shows the failure.

The predicates are built in topological order from the join rules; an atom outside the fragment of
the solver is replaced so that the result stays sufficient (``runs.bounds``), and a predicate that
grows past ``MAX_SIZE`` nodes is dropped (the fact is lost, never invented).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from flowstat.analysis.runs import bounds, conj, disj, negate, run_conditions, size
from flowstat.analysis.signatures import Signatures
from flowstat.analysis.variables import lower_guards
from flowstat.analysis.wording import describe
from flowstat.compiler.plan import Plan, PlanStep
from flowstat.dsl.ast import (
    Attr,
    Binary,
    Call,
    Compare,
    Expr,
    Index,
    ListLit,
    Literal,
    Name,
    Ref,
    Unary,
    to_source,
)
from flowstat.model.diagnostics import Diagnostic, Severity
from flowstat.solver.domains import Domain
from flowstat.solver.predicate import FALSE, TRUE, Pred
from flowstat.solver.solve import Status, check, entails, uncovered

MAX_SIZE = 300  # nodes of one predicate; past it the fact is dropped


@dataclass(frozen=True)
class Availability:
    runs: Mapping[str, Pred]  # when the step runs (no action fails)
    guaranteed: Mapping[str, Mapping[str, Pred]]  # Av: step -> ancestor -> condition
    counts: Mapping[str, Mapping[str, int]]  # step -> foreach step -> instances guaranteed


def compute(plan: Plan, preds: Mapping[str, Pred]) -> Availability:
    """The run condition, the guaranteed facts and the guaranteed group sizes of every step."""
    runs = run_conditions(plan, preds)
    low = {s: bounds(r)[0] for s, r in runs.items()}
    high = {s: bounds(r)[1] for s, r in runs.items()}
    clean = _clean(plan)
    guaranteed: dict[str, dict[str, Pred]] = {}
    counts: dict[str, dict[str, int]] = {}
    for step in plan.steps:
        facts = _facts(step, guaranteed, high)
        for ancestor in _ancestors(plan, step):
            if clean[ancestor] and low[ancestor] != FALSE:
                facts[ancestor] = _keep(disj([facts.get(ancestor, FALSE), low[ancestor]]))
        guaranteed[step.id] = {x: p for x, p in facts.items() if p != FALSE}
        counts[step.id] = _counts(plan, step, counts)
    return Availability(runs, guaranteed, counts)


def _keep(p: Pred) -> Pred:
    return FALSE if size(p) > MAX_SIZE else p


def _ancestors(plan: Plan, step: PlanStep) -> list[str]:
    bits = plan.ancestor_bits[plan.steps.index(step)]
    return [s.id for j, s in enumerate(plan.steps) if bits >> j & 1]


def _clean(plan: Plan) -> dict[str, bool]:
    """Whether no step at or above ``X`` has ``on_failure: continue``: X ran iff it succeeded."""
    out: dict[str, bool] = {}
    for step in plan.steps:
        out[step.id] = step.on_failure != "continue" and all(out[d] for d in step.needs)
    return out


def _facts(
    step: PlanStep,
    guaranteed: Mapping[str, Mapping[str, Pred]],
    high: Mapping[str, Pred],
) -> dict[str, Pred]:
    """The facts that the join rule of ``step`` passes on from its dependencies (spec 10.1)."""
    kind = step.join.kind
    if not step.needs or kind == "all_done":
        return {}
    each: list[dict[str, Pred]] = []
    for need in step.needs:
        known: dict[str, Pred] = {need: TRUE}
        for x, p in guaranteed[need].items():
            known[x] = disj([known.get(x, FALSE), p])
        each.append(known)
    names = set().union(*each)
    out: dict[str, Pred] = {}
    for x in names:
        if kind == "all_success":
            out[x] = _keep(disj([facts.get(x, FALSE) for facts in each]))
        else:  # any_success, at_least: every dependency that succeeded must pass the fact on
            terms: list[Pred] = []
            for need, facts in zip(step.needs, each, strict=True):
                terms.append(disj([negate(high[need]), facts.get(x, FALSE)]))
            out[x] = _keep(conj(terms))
    return out


def _counts(plan: Plan, step: PlanStep, counts: Mapping[str, Mapping[str, int]]) -> dict[str, int]:
    """How many instances of each foreach step have surely succeeded when ``step`` starts."""
    kind = step.join.kind
    if not step.needs or kind == "all_done":
        return {}
    groups = {plan.step(n).base for n in step.needs if plan.step(n).loop is not None}
    inherited = [counts[n] for n in step.needs]
    for member in step.needs:
        groups |= set(counts[member])
    out: dict[str, int] = {}
    for group in groups:
        members = sum(1 for n in step.needs if plan.step(n).base == group)
        others = len(step.needs) - members
        if kind == "all_success":
            direct = members
            below = max((c.get(group, 0) for c in inherited), default=0)
        else:
            quorum = 1 if kind == "any_success" else step.join.k
            direct = max(0, min(members, quorum - others))
            below = min((c.get(group, 0) for c in inherited), default=0)
        got = max(direct, below)
        if got:
            out[group] = got
    return out


# -- references --------------------------------------------------------------------------------
def references(
    expr: Expr, proven: frozenset[str] = frozenset()
) -> Iterator[tuple[Ref, frozenset[str]]]:
    """Each reference of an expression with the steps that ``exists`` has proved before it.

    In ``a and b`` the conjuncts ``exists(${x.f})`` of ``a`` prove ``x`` for ``b`` (spec 10.2). The
    argument of ``exists`` itself is not a reference that must exist.
    """
    match expr:
        case Ref():
            yield expr, proven
        case Binary(op="and", left=left, right=right):
            yield from references(left, proven)
            yield from references(right, proven | _proved(left))
        case Call(func=Name(name="exists")):
            return
        case Call(func=func, args=args):
            yield from references(func, proven)
            for arg in args:
                yield from references(arg, proven)
        case Binary(left=left, right=right) | Compare(left=left, right=right):
            yield from references(left, proven)
            yield from references(right, proven)
        case Unary(operand=operand):
            yield from references(operand, proven)
        case Attr(obj=obj):
            yield from references(obj, proven)
        case Index(obj=obj, index=index):
            yield from references(obj, proven)
            yield from references(index, proven)
        case ListLit(items=items):
            for item in items:
                yield from references(item, proven)
        case Literal() | Name():
            return


def _proved(expr: Expr) -> frozenset[str]:
    """The steps whose output a conjunction proves to exist with ``exists``."""
    match expr:
        case Call(func=Name(name="exists"), args=(Ref(path=path),)):
            return frozenset({str(path[0].value)})
        case Binary(op="and", left=left, right=right):
            return _proved(left) | _proved(right)
        case _:
            return frozenset()


def _target(ref: Ref) -> str | None:
    """The step a reference reads, or ``None`` for an input or a whole foreach step."""
    head = str(ref.path[0].value)
    if head == "inputs" or any(p.kind == "star" for p in ref.path):
        return None
    return head


# -- the analysis ------------------------------------------------------------------------------
def analyse(plan: Plan, signatures: Signatures) -> list[Diagnostic]:
    """Warnings and notes about availability, groups, goals, dead steps and undo (spec 10)."""
    lowered = lower_guards(plan, signatures)
    result = compute(plan, lowered.preds)
    out: list[Diagnostic] = []
    seen: set[str] = set()
    for step in plan.steps:
        if step.base in seen:
            continue
        seen.add(step.base)
        out.extend(_references(plan, step, result, lowered.preds, lowered.domains))
        out.extend(_groups(plan, step, result))
        out.extend(_undo(plan, step, signatures))
    out.extend(_dead(plan, result, lowered.preds, lowered.domains))
    out.extend(_goals(plan, result, lowered.domains))
    return sorted(out, key=lambda d: (d.line, d.column, d.code))


def available_at(plan: Plan, signatures: Signatures, step_id: str) -> set[str]:
    """The steps whose output can be read in ``step_id`` without a warning (for the editor)."""
    lowered = lower_guards(plan, signatures)
    result = compute(plan, lowered.preds)
    guard = lowered.preds.get(step_id, TRUE)
    return {
        x
        for x, p in result.guaranteed[step_id].items()
        if entails(guard, p, lowered.domains).status is Status.UNSAT
    }


def _diag(code: str, severity: Severity, message: str, line: int, column: int) -> Diagnostic:
    return Diagnostic(code, severity, message, line, column)


def _references(
    plan: Plan,
    step: PlanStep,
    result: Availability,
    preds: Mapping[str, Pred],
    domains: dict[str, Domain],
) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    own = preds.get(step.id, TRUE)
    uses: list[tuple[Ref, frozenset[str], Pred]] = []
    if step.when is not None:
        uses += [(r, p, TRUE) for r, p in references(step.when)]
    if step.ensures is not None:
        uses += [(r, p, own) for r, p in references(step.ensures)]
    for _key, parts in step.with_:
        uses += [(p, frozenset(), own) for p in parts if isinstance(p, Ref)]
    reported: set[tuple[str, int, int]] = set()
    ancestors = set(_ancestors(plan, step))
    for ref, proven, assumption in uses:
        target = _target(ref)
        if target is None or target == step.id or target in proven:
            continue
        if target not in ancestors:
            continue
        fact = result.guaranteed[step.id].get(target, FALSE)
        key = (target, ref.line, ref.column)
        if key in reported:
            continue
        reported.add(key)
        answer = entails(assumption, fact, domains)
        if answer.status is Status.UNSAT:
            continue
        shown = to_source(ref)
        if answer.status is Status.UNKNOWN:
            out.append(
                _diag(
                    "I-AVAIL-003",
                    Severity.INFO,
                    f"could not decide whether {shown} exists when '{step.id}' starts: "
                    f"a condition is outside what the analysis understands",
                    ref.line,
                    ref.column,
                )
            )
            continue
        out.append(
            _diag(
                "W-AVAIL-001",
                Severity.WARNING,
                f"{shown} may not exist: {_reason(plan, target, answer.witness)}",
                ref.line,
                ref.column,
            )
        )
    return out


def _reason(plan: Plan, target: str, witness: dict[str, object] | None) -> str:
    """Why the step ``target`` is not certain to have succeeded, in words."""
    step = plan.step(target)
    causes: list[str] = []
    if step.when is not None:
        causes.append(f"it is skipped when {to_source(step.when)} is false")
    if step.on_failure == "continue":
        causes.append("it can fail without stopping the run (on_failure: continue)")
    if not causes:
        causes.append("a step before it can be skipped or can fail")
    text = f"'{step.id}' is not guaranteed to have succeeded: " + " and ".join(causes)
    if witness:
        text += f"; for example when {describe(witness)}"
    return text


# -- groups ------------------------------------------------------------------------------------
def _groups(plan: Plan, step: PlanStep, result: Availability) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    uses: list[Expr] = [e for e in (step.when, step.ensures) if e is not None]
    for expr in uses:
        for needed, node in _needs(expr):
            group = str(node.path[0].value)
            have = result.counts[step.id].get(group, 0)
            if needed > have:
                out.append(
                    _diag(
                        "W-AVAIL-002",
                        Severity.WARNING,
                        f"{to_source(node)} is used as if it had {needed} element"
                        f"{'s' if needed != 1 else ''}, but only {have} of the instances of "
                        f"'{group}' are guaranteed to have succeeded when '{step.id}' starts",
                        node.line,
                        node.column,
                    )
                )
    return out


def _needs(expr: Expr) -> Iterator[tuple[int, Ref]]:
    """References to a whole group with the number of elements the expression relies on."""
    match expr:
        case Index(obj=Ref() as ref, index=Literal(value=int() as i)) if _star(ref):
            yield i + 1, ref
        case Call(func=Name(name="min" | "max"), args=(Ref() as ref,)) if _star(ref):
            yield 1, ref
        case Binary(left=left, right=right) | Compare(left=left, right=right):
            yield from _needs(left)
            yield from _needs(right)
        case Unary(operand=operand):
            yield from _needs(operand)
        case Attr(obj=obj):
            yield from _needs(obj)
        case Index(obj=obj, index=index):
            yield from _needs(obj)
            yield from _needs(index)
        case Call(args=args):
            for arg in args:
                yield from _needs(arg)
        case ListLit(items=items):
            for item in items:
                yield from _needs(item)
        case _:
            return


def _star(ref: Ref) -> bool:
    return any(p.kind == "star" for p in ref.path)


# -- dead steps, goals, undo -------------------------------------------------------------------
def _dead(
    plan: Plan, result: Availability, preds: Mapping[str, Pred], domains: dict[str, Domain]
) -> list[Diagnostic]:
    """A step that can never run because of the steps before it (its own guard is C6's, 8.2)."""
    dead: dict[str, bool] = {}
    out: list[Diagnostic] = []
    for step in plan.steps:
        own = preds.get(step.id)
        own_dead = own is not None and check(own, domains).status is Status.UNSAT
        gone = own_dead or check(result.runs[step.id], domains).status is Status.UNSAT
        dead[step.id] = gone
        if not gone or own_dead or step.loop is not None:
            continue
        cause = next((n for n in step.needs if dead[n]), None)
        why = (
            f"'{cause}' can never run"
            if cause is not None
            else f"its join ({step.join.kind}) can never be satisfied"
        )
        out.append(
            _diag(
                "W-DEAD-001",
                Severity.WARNING,
                f"'{step.id}' can never run: {why}",
                step.line,
                step.column,
            )
        )
    return out


def _goals(plan: Plan, result: Availability, domains: dict[str, Domain]) -> list[Diagnostic]:
    if not plan.goals:
        return []
    where = {g: p for g, p in zip(plan.goals, plan.goal_positions, strict=False)}
    first = where.get(
        plan.goals[0], (plan.step(plan.goals[0]).line, plan.step(plan.goals[0]).column)
    )
    if plan.goal_kind == "any_of":
        answer = uncovered([result.runs[g] for g in plan.goals], domains)
        if answer.status is Status.SAT:
            message = "no goal is reached" + _when(answer.witness)
            return [_diag("W-GOAL-001", Severity.WARNING, message, *first)]
        if answer.status is Status.UNKNOWN:
            message = (
                "could not decide whether some goal is always reached: "
                "a condition is outside what the analysis understands"
            )
            return [_diag("I-AVAIL-003", Severity.INFO, message, *first)]
        return []
    out: list[Diagnostic] = []
    for goal in plan.goals:
        answer = check(negate(result.runs[goal]), domains)
        if answer.status is Status.SAT:
            message = f"the goal '{goal}' may be skipped" + _when(answer.witness)
            out.append(_diag("W-GOAL-001", Severity.WARNING, message, *where.get(goal, first)))
    return out


def _when(witness: dict[str, object] | None) -> str:
    return f", for example when {describe(witness)}" if witness else ""


def _undo(plan: Plan, step: PlanStep, signatures: Signatures) -> list[Diagnostic]:
    """A step with an effect and no ``compensate``, when the workflow undoes failed runs (10.5)."""
    if not any(s.on_failure == "compensate" for s in plan.steps):
        return []
    signature = signatures.get(step.action)
    if signature is None or not signature.effect or step.compensate is not None:
        return []
    return [
        _diag(
            "W-COMP-001",
            Severity.WARNING,
            f"'{step.id}' changes something outside the engine ({step.action}) but has no "
            "'compensate'; when a run is undone this step stays done",
            step.line,
            step.column,
        )
    ]
