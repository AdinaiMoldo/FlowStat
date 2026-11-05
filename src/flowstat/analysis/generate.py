"""Generator of test inputs (US-24, C6): a small set of cases that run and skip every guarded step.

A target is "step s runs" or "step s is skipped", for each step that has a ``when`` (steps that are
instances of a ``foreach`` are not targets). The exact condition under which a step runs is built
from the plan: its guard, and the join rule over the conditions of its dependencies (the optimistic
run of C2 in the small). For each target the solver is asked for a case; a target whose condition is
unsatisfiable is proved impossible. The cases are then widened with the boundary values of every
comparison and every value of every enumeration, what each candidate really covers is decided by
``predict`` (so the cover does not rest on the solver being right), and a greedy set cover picks a
small set. Everything is deterministic: the same plan gives the same cases.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction

from flowstat.analysis.predict import RUN, SKIP, predict
from flowstat.analysis.runs import run_conditions
from flowstat.analysis.signatures import Signatures
from flowstat.analysis.variables import lower_guards
from flowstat.compiler.plan import Plan
from flowstat.solver.domains import BoolSet, Domain, NumberSet, StringSet
from flowstat.solver.predicate import (
    And,
    Cmp,
    Diff,
    Member,
    Not,
    Or,
    Pred,
)
from flowstat.solver.solve import Status, check

MAX_CANDIDATES = 600  # variations of the base cases that are tried
SEARCH_BUDGET = 40_000  # nodes of the search for a cover smaller than the greedy one


@dataclass(frozen=True, slots=True)
class Target:
    step: str
    runs: bool  # True: the step runs; False: it is skipped

    @property
    def label(self) -> str:
        return f"{self.step} runs" if self.runs else f"{self.step} is skipped"


@dataclass(frozen=True, slots=True)
class Case:
    """Values for one run: the inputs that guards read, the outputs the mocked actions return."""

    inputs: dict[str, object]
    outputs: dict[str, dict[str, object]]  # step -> the fields that guards read
    covers: tuple[Target, ...]  # every target that the prediction says this case reaches

    def values(self) -> dict[str, object]:
        """The case as ``variable -> value`` (``inputs.x``, ``step.field``)."""
        out: dict[str, object] = {f"inputs.{k}": v for k, v in self.inputs.items()}
        for step, fields in self.outputs.items():
            out.update({f"{step}.{k}": v for k, v in fields.items()})
        return out


@dataclass(frozen=True, slots=True)
class CaseSet:
    targets: tuple[Target, ...]
    cases: tuple[Case, ...]
    infeasible: tuple[Target, ...]  # proved: no values make it happen
    missed: tuple[Target, ...]  # no case found; nothing is proved


def generate(plan: Plan, signatures: Signatures) -> CaseSet:
    lowered = lower_guards(plan, signatures)
    domains = lowered.domains
    runs = run_conditions(plan, lowered.preds)
    targets = tuple(
        Target(step.id, runs_)
        for step in plan.steps
        if step.when is not None and step.loop is None
        for runs_ in (True, False)
    )
    infeasible: list[Target] = []
    bases: list[dict[str, object]] = []
    for target in targets:
        condition = runs[target.step] if target.runs else Not(runs[target.step])
        result = check(condition, domains)
        if result.status is Status.UNSAT:
            infeasible.append(target)
        elif result.status is Status.SAT and result.witness is not None:
            bases.append(_complete(result.witness, domains))
    candidates = _candidates(bases, domains, lowered.preds)
    feasible = [t for t in targets if t not in infeasible]
    reach = [_reached(plan, lowered.preds, c, targets) for c in candidates]
    chosen, uncovered = _greedy(reach, set(feasible))
    better = _smaller_cover(reach, set(feasible) - uncovered, len(chosen))
    if better is not None:
        chosen = better
    cases = tuple(_case(candidates[i], reach[i], targets) for i in chosen)
    missed = tuple(t for t in feasible if t in uncovered)
    return CaseSet(targets, cases, tuple(infeasible), missed)


# -- the cover -------------------------------------------------------------------------------
def _greedy(reach: list[frozenset[Target]], universe: set[Target]) -> tuple[list[int], set[Target]]:
    """The candidate that reaches most of what is still open, again; ties go to the first."""
    chosen: list[int] = []
    uncovered = set(universe)
    while uncovered:
        best, gain = -1, 0
        for i, reached in enumerate(reach):
            new = len(reached & uncovered)
            if new > gain:
                best, gain = i, new
        if best < 0:
            break
        chosen.append(best)
        uncovered -= reach[best]
    return chosen, uncovered


def _smaller_cover(
    reach: list[frozenset[Target]], universe: set[Target], size: int
) -> list[int] | None:
    """A cover of ``universe`` with fewer than ``size`` candidates, if one is found in the budget.

    The greedy cover can be one case too many (it takes the candidate that reaches most first,
    and two candidates that fit together may each reach less). Branch and bound over the distinct
    reach sets, with a cover of k candidates sought for k = 1, 2, ...; the target with the fewest
    candidates is branched on, so the search is small for the workflows that occur.
    """
    unique: dict[frozenset[Target], int] = {}
    for i, reached in enumerate(reach):
        useful = reached & universe
        if useful and useful not in unique:
            unique[useful] = i
    sets = [s for s in unique if not any(s < other for other in unique)]
    index = {s: unique[s] for s in sets}
    budget = [SEARCH_BUDGET]

    def search(open_: set[Target], left: int) -> list[frozenset[Target]] | None:
        if not open_:
            return []
        budget[0] -= 1
        if left == 0 or budget[0] < 0:
            return None
        gains = {s: len(s & open_) for s in sets}
        if left * max(gains.values(), default=0) < len(open_):
            return None
        target = min(open_, key=lambda t: (sum(1 for s in sets if t in s), t.step, t.runs))
        for s in sorted((s for s in sets if target in s), key=lambda s: (-gains[s], index[s])):
            rest = search(open_ - s, left - 1)
            if rest is not None:
                return [s, *rest]
        return None

    for k in range(1, size):
        found = search(set(universe), k)
        if budget[0] < 0:
            return None
        if found is not None:
            return [index[s] for s in found]
    return None


# -- candidates -------------------------------------------------------------------------------
def _complete(witness: dict[str, object], domains: dict[str, Domain]) -> dict[str, object]:
    """Values for every variable: the witness where it speaks, a sample of the domain elsewhere."""
    out: dict[str, object] = {}
    for name in sorted(domains):
        out[name] = witness[name] if name in witness else _sample(domains[name])
    return {k: _plain(v, domains[k]) for k, v in out.items()}


def _sample(domain: Domain) -> object:
    if domain.kind in ("int", "real"):
        return NumberSet.of(domain).sample()
    if domain.kind == "bool":
        return BoolSet.of(domain).sample()
    return StringSet.of(domain).sample()


def _plain(value: object, domain: Domain) -> object:
    """A value as the engine will see it: an integer, a float, a string or a boolean."""
    if isinstance(value, Fraction):
        if domain.kind == "int" or value.denominator == 1:
            return int(value)
        return float(value)
    return value


def _candidates(
    bases: list[dict[str, object]], domains: dict[str, Domain], preds: Mapping[str, Pred]
) -> list[dict[str, object]]:
    seen: set[tuple[tuple[str, object], ...]] = set()
    out: list[dict[str, object]] = []

    def add(candidate: dict[str, object]) -> None:
        key = tuple(sorted(candidate.items(), key=lambda kv: kv[0]))
        if key not in seen:
            seen.add(key)
            out.append(candidate)

    for base in bases:
        add(base)
    values = {name: _values(name, domain, preds) for name, domain in domains.items()}
    for base in bases:
        for name in sorted(domains):
            for value in values[name]:
                if len(out) >= MAX_CANDIDATES:
                    return out
                if value != base[name] or type(value) is not type(base[name]):
                    add({**base, name: value})
    return out


def _values(name: str, domain: Domain, preds: Mapping[str, Pred]) -> Sequence[object]:
    """Values worth trying for a variable: its enumeration, or the boundaries of its comparisons."""
    if domain.kind == "bool":
        return [False, True]
    if domain.kind == "str":
        if domain.values is not None:
            return sorted(str(v) for v in domain.values)
        found = {c for c in _constants(name, preds) if isinstance(c, str)}
        return sorted(found | {str(StringSet.of(domain).sample())})
    allowed = NumberSet.of(domain)
    step = Fraction(1) if domain.kind == "int" else Fraction(1, 2)
    points: set[Fraction] = set()
    for c in _constants(name, preds):
        if isinstance(c, Fraction):
            points |= {c - step, c, c + step}
    for bound in (domain.lo, domain.hi):
        if bound is not None:
            points.add(bound)
    return [_plain(p, domain) for p in sorted(points) if allowed.contains(p)]


def _constants(name: str, preds: Mapping[str, Pred]) -> list[Fraction | str]:
    found: list[Fraction | str] = []

    def walk(p: Pred) -> None:
        match p:
            case Cmp(var=var, const=const) if var == name:
                found.append(const)
            case Member(var=var, consts=consts) if var == name:
                found.extend(consts)
            case And(items=items) | Or(items=items):
                for item in items:
                    walk(item)
            case Not(item=item):
                walk(item)
            case Diff():
                pass

    for pred in preds.values():
        walk(pred)
    return found


# -- what a candidate covers ------------------------------------------------------------------
def _reached(
    plan: Plan, preds: Mapping[str, Pred], values: dict[str, object], targets: tuple[Target, ...]
) -> frozenset[Target]:
    states = predict(plan, preds, values)
    return frozenset(t for t in targets if states[t.step] == (RUN if t.runs else SKIP))


def _case(
    values: dict[str, object], reached: frozenset[Target], targets: tuple[Target, ...]
) -> Case:
    inputs: dict[str, object] = {}
    outputs: dict[str, dict[str, object]] = {}
    for name in sorted(values):
        owner, _, field = name.rpartition(".")
        if owner == "inputs":
            inputs[field] = values[name]
        else:
            outputs.setdefault(owner, {})[field] = values[name]
    return Case(inputs, outputs, tuple(t for t in targets if t in reached))
