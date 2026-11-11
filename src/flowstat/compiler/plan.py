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
    max: int  # retries after the first attempt
    backoff: str  # fixed or exponential
    delay: float = 1.0  # seconds before the first retry


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
    timeout: float | None = None  # seconds a step that waits for a person may wait (US-38)


@dataclass(frozen=True, slots=True)
class InputSpec:
    name: str
    type: str  # string, int, float, bool, date or list<...>
    pattern: str | None
    ref: str | None  # catalogue list the value must come from
    has_default: bool
    default: object
    required: bool  # a value must be given (false when there is a default or required: false)
    line: int
    column: int
    values: tuple[object, ...] | None = None  # the allowed constants (a closed list)
    minimum: float | None = None  # inclusive bounds of a number
    maximum: float | None = None


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
    inputs: tuple[InputSpec, ...] = ()
    goal_positions: tuple[tuple[int, int], ...] = ()  # (line, column) of each goal in the text
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
