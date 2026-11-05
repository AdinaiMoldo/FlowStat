"""Scheduler core (C3, level 1): a pure state machine. Events in, commands out.

The scheduler never starts a thread, never sleeps and never reads a clock: the driver (a thread
pool in production, a simulated clock in tests) executes the commands and reports what happened,
with the time of the event. This is what makes deterministic mode and exact tests possible.

A step is decided only when all its predecessors are terminal, so the result of a run does not
depend on timing. Ready steps start in the order of the longest chain behind them (the plain
critical path; the feasible path replaces this priority in M5), then in declaration order.
"""

from __future__ import annotations

import heapq
from collections import ChainMap, deque
from collections.abc import Mapping
from dataclasses import dataclass

from flowstat.compiler.plan import JoinSpec, Plan
from flowstat.dsl.ast import to_source
from flowstat.engine.evaluator import EvalContext, EvalError, evaluate_bool, render_template
from flowstat.model.events import Event, EventKind
from flowstat.model.states import TERMINAL_STEP_STATES, RunOutcome, StepState


@dataclass(frozen=True, slots=True)
class Start:
    step: str
    action: str
    args: dict[str, object]


@dataclass(frozen=True, slots=True)
class Cancel:
    step: str


Command = Start | Cancel

_FAILED_LIKE = frozenset({StepState.FAILED, StepState.UPSTREAM_FAILED, StepState.CANCELLED})


def _join_verdict(join: JoinSpec, states: list[StepState]) -> StepState | None:
    """None when the step may run, else the terminal state it takes: SKIPPED or UPSTREAM_FAILED."""
    if not states or join.kind == "all_done":
        return None
    succeeded = sum(1 for s in states if s is StepState.SUCCEEDED)
    failed = sum(1 for s in states if s in _FAILED_LIKE)
    if join.kind == "all_success":
        if succeeded == len(states):
            return None
        return StepState.UPSTREAM_FAILED if failed else StepState.SKIPPED
    needed = 1 if join.kind == "any_success" else join.k
    if succeeded >= needed:
        return None
    # a shortfall that the failures could have filled is a failure; otherwise the step is skipped
    return (
        StepState.UPSTREAM_FAILED if failed and succeeded + failed >= needed else StepState.SKIPPED
    )


class Scheduler:
    def __init__(
        self,
        plan: Plan,
        inputs: Mapping[str, object],
        max_parallel: int,
        durations: Mapping[str, float] | None = None,
    ) -> None:
        if max_parallel < 1:
            raise ValueError("max_parallel must be at least 1")
        self.plan = plan
        self.inputs = dict(inputs)
        self.max_parallel = max_parallel
        steps = plan.steps
        n = len(steps)
        index = {s.id: i for i, s in enumerate(steps)}
        self._preds = [[index[p] for p in s.needs] for s in steps]
        self._succs: list[list[int]] = [[] for _ in range(n)]
        for i, preds in enumerate(self._preds):
            for p in preds:
                self._succs[p].append(i)
        weight = durations or {}
        self._priority = [0.0] * n
        for i in reversed(range(n)):  # plan.steps is topological
            tail = max((self._priority[j] for j in self._succs[i]), default=0.0)
            self._priority[i] = weight.get(steps[i].id, 1.0) + tail
        self._index = index
        self._instances = {s.base: plan.instances_of(s.base) for s in steps if s.loop is not None}
        self.states: dict[str, StepState] = {s.id: StepState.PENDING for s in steps}
        self.outputs: dict[str, dict[str, object]] = {}
        self.errors: dict[str, tuple[str, str]] = {}
        self.events: list[Event] = []
        self.outcome: RunOutcome | None = None
        self._remaining = [len(p) for p in self._preds]
        self._ready: list[tuple[float, int]] = []
        self._queue: deque[int] = deque()
        self._running: set[int] = set()
        self._waiting: set[int] = set()
        self._commands: list[Command] = []
        self._terminal = 0
        self._started = False
        self._stopped = False
        self._cancelled = False
        self._ctx = EvalContext(self.inputs, self.outputs, self._instances)

    # -- what the driver may ask ---------------------------------------------------------------
    @property
    def status(self) -> str:
        if self.outcome is not None:
            return "FINISHED"
        if not self._started:
            return "CREATED"
        if self._running or self._ready or self._queue:
            return "RUNNING"
        return "WAITING" if self._waiting else "RUNNING"

    # -- events from the driver ----------------------------------------------------------------
    def start(self, now: float) -> list[Command]:
        if self._started:
            return []
        self._started = True
        self._log(EventKind.RUN_STARTED, now)
        self._queue.extend(i for i, r in enumerate(self._remaining) if r == 0)
        return self._advance(now)

    def step_done(self, step_id: str, output: dict[str, object], now: float) -> list[Command]:
        i = self._index.get(step_id)
        if i is None or self.states[step_id] is not StepState.RUNNING:
            return []
        return self._complete(i, output, now)

    def signal(self, step_id: str, output: dict[str, object], now: float) -> list[Command]:
        """The answer a waiting step was paused for; ignored when the step is not waiting."""
        i = self._index.get(step_id)
        if i is None or self.states[step_id] is not StepState.WAITING:
            return []
        return self._complete(i, output, now)

    def step_failed(self, step_id: str, code: str, message: str, now: float) -> list[Command]:
        i = self._index.get(step_id)
        if i is None or self.states[step_id] not in (StepState.RUNNING, StepState.WAITING):
            return []
        self._finish(i, StepState.FAILED, now, code, message)
        return self._advance(now)

    def step_waiting(self, step_id: str, now: float) -> list[Command]:
        i = self._index.get(step_id)
        if i is None or self.states[step_id] is not StepState.RUNNING:
            return []
        self.states[step_id] = StepState.WAITING
        self._running.discard(i)
        self._waiting.add(i)
        self._log(EventKind.STEP_WAITING, now, step=step_id)
        return self._advance(now)

    def cancel(self, now: float) -> list[Command]:
        if self.outcome is not None:
            return []
        self._started = True
        self._cancelled = True
        self._stop(now, keep=-1)
        return self._advance(now)

    # -- internals -----------------------------------------------------------------------------
    def _log(self, kind: EventKind, now: float, **fields: object) -> None:
        self.events.append(Event(len(self.events), kind, now, **fields))  # type: ignore[arg-type]

    def _flush(self) -> list[Command]:
        commands, self._commands = self._commands, []
        return commands

    def _fail_text(self, error: EvalError) -> str:
        return f"{error.message} (line {error.line}, column {error.column})"

    def _complete(self, i: int, output: dict[str, object], now: float) -> list[Command]:
        step = self.plan.steps[i]
        if step.ensures is not None:
            visible: ChainMap[str, Mapping[str, object]] = ChainMap(
                {step.id: output},
                self.outputs,  # type: ignore[arg-type]
            )
            ctx = EvalContext(self.inputs, visible, self._instances)
            try:
                holds = evaluate_bool(step.ensures, ctx)
            except EvalError as error:
                self._finish(i, StepState.FAILED, now, error.code, self._fail_text(error))
                return self._advance(now)
            if not holds:
                message = f"contract violated: {to_source(step.ensures)}"
                self._finish(i, StepState.FAILED, now, "E-CONTRACT-001", message)
                return self._advance(now)
        self.outputs[step.id] = output
        self._finish(i, StepState.SUCCEEDED, now, output=output)
        return self._advance(now)

    def _finish(
        self,
        i: int,
        state: StepState,
        now: float,
        code: str | None = None,
        message: str | None = None,
        output: dict[str, object] | None = None,
    ) -> None:
        step = self.plan.steps[i]
        self.states[step.id] = state
        self._running.discard(i)
        self._waiting.discard(i)
        self._terminal += 1
        if code is not None:
            self.errors[step.id] = (code, message or "")
        self._log(
            EventKind.STEP_FINISHED,
            now,
            step=step.id,
            state=state,
            code=code,
            message=message,
            output=output,
        )
        for j in self._succs[i]:
            self._remaining[j] -= 1
            if self._remaining[j] == 0:
                self._queue.append(j)
        if state is StepState.FAILED and step.on_failure == "fail":
            self._stop(now, keep=i)

    def _stop(self, now: float, keep: int) -> None:
        """Stop the run: cancel everything that is not finished (``keep`` is the failed step)."""
        self._stopped = True
        self._ready.clear()
        self._queue.clear()
        for j, step in enumerate(self.plan.steps):
            state = self.states[step.id]
            if j == keep or state in TERMINAL_STEP_STATES:
                continue
            if state is StepState.RUNNING:
                self._commands.append(Cancel(step.id))
            self.states[step.id] = StepState.CANCELLED
            self._running.discard(j)
            self._waiting.discard(j)
            self._terminal += 1
            self._log(EventKind.STEP_FINISHED, now, step=step.id, state=StepState.CANCELLED)

    def _decide(self, i: int, now: float) -> None:
        """All predecessors are terminal: skip, fail or make the step ready."""
        step = self.plan.steps[i]
        if self.states[step.id] is not StepState.PENDING:
            return
        verdict = _join_verdict(
            step.join, [self.states[self.plan.steps[j].id] for j in self._preds[i]]
        )
        if verdict is not None:
            self._finish(i, verdict, now)
            return
        if step.when is not None:
            try:
                holds = evaluate_bool(step.when, self._ctx)
            except EvalError as error:
                self._finish(i, StepState.FAILED, now, error.code, self._fail_text(error))
                return
            if not holds:
                self._finish(i, StepState.SKIPPED, now)
                return
        self.states[step.id] = StepState.READY
        heapq.heappush(self._ready, (-self._priority[i], i))

    def _dispatch_one(self, now: float) -> None:
        _, i = heapq.heappop(self._ready)
        step = self.plan.steps[i]
        try:
            args = {name: render_template(parts, self._ctx) for name, parts in step.with_}
        except EvalError as error:
            self._finish(i, StepState.FAILED, now, error.code, self._fail_text(error))
            return
        self.states[step.id] = StepState.RUNNING
        self._running.add(i)
        self._log(EventKind.STEP_STARTED, now, step=step.id)
        self._commands.append(Start(step.id, step.action, args))

    def _advance(self, now: float) -> list[Command]:
        while True:
            while self._queue and not self._stopped:
                self._decide(self._queue.popleft(), now)
            if self._stopped or not self._ready or len(self._running) >= self.max_parallel:
                break
            self._dispatch_one(now)
        self._maybe_finish(now)
        return self._flush()

    def _maybe_finish(self, now: float) -> None:
        if self.outcome is not None or not self._started:
            return
        if self._terminal < len(self.plan.steps):
            return
        if self._cancelled:
            self.outcome = RunOutcome.CANCELLED
        elif self._stopped:
            self.outcome = RunOutcome.FAILED
        else:
            reached = [self.states[g] is StepState.SUCCEEDED for g in self.plan.goals]
            holds = all(reached) if self.plan.goal_kind == "all_of" else any(reached)
            self.outcome = RunOutcome.SUCCEEDED if holds else RunOutcome.GOAL_NOT_REACHED
        self._log(EventKind.RUN_FINISHED, now, outcome=self.outcome)
