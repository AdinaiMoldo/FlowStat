"""The compiled plan: a static graph of step instances, in topological order.

Every layer above the compiler (analysis, engine, recovery, migration) reads this structure and
nothing else about the workflow text.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from flowstat.dsl.ast import Expr


@dataclass(frozen=True, slots=True)
class JoinSpec:
    kind: str  # all_success, any_success, all_done, at_least
    k: int = 0


@dataclass(frozen=True, slots=True)
class RetrySpec:
    max: int
    backoff: str


@dataclass(frozen=True, slots=True)
class PlanStep:
    id: str  # "publish" or "load[BIS]"
    base: str  # the step of the source text this instance comes from
    action: str
    needs: tuple[str, ...]
    join: JoinSpec
    when: Expr | None
    ensures: Expr | None
    with_: tuple[tuple[str, tuple[object, ...]], ...]  # parts: str, Ref or a plain value
    on_failure: str
    retry: RetrySpec | None
    compensate: str | None
    loop: tuple[str, str] | None  # (variable, catalogue code) for an expanded instance
    line: int  # position of the step in the source text (the source map)
    column: int


@dataclass(frozen=True)
class Plan:
    name: str
    version: int
    steps: tuple[PlanStep, ...]  # topological order
    goal_kind: str  # any_of or all_of
    goals: tuple[str, ...]
    ancestor_bits: tuple[
        int, ...
    ]  # bit j of entry i is set when steps[j] is an ancestor of steps[i]
    _index: dict[str, int] = field(init=False, repr=False, compare=False, default_factory=dict)

    def __post_init__(self) -> None:
        self._index.update({s.id: i for i, s in enumerate(self.steps)})

    def step(self, step_id: str) -> PlanStep:
        return self.steps[self._index[step_id]]

    def instances_of(self, base: str) -> tuple[str, ...]:
        return tuple(s.id for s in self.steps if s.base == base)

    def ancestors(self, step_id: str) -> set[str]:
        bits = self.ancestor_bits[self._index[step_id]]
        return {s.id for j, s in enumerate(self.steps) if bits >> j & 1}
