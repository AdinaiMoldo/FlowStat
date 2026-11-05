"""Scheduler core (C3, level 1): a pure state machine. Events in, commands out.

The scheduler never starts a thread, never sleeps and never reads a clock: the driver (a thread
pool in production, a simulated clock in tests) executes the commands and reports what happened,
with the time of the event. This is what makes deterministic mode and exact tests possible.

A step is decided only when all its predecessors are terminal, so the result of a run does not
depend on timing. Ready steps start in the order of the longest chain behind them (the plain
critical path; the feasible path replaces this priority in M5), then in declaration order. The
policy ``fifo`` (in the order of becoming ready) exists as the baseline of the benchmark.
"""

from __future__ import annotations

import heapq
from collections import ChainMap, deque
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass

from flowstat.compiler.plan import JoinSpec, Plan, RetrySpec
from flowstat.dsl.ast import to_source
from flowstat.engine.evaluator import EvalContext, EvalError, evaluate_bool, render_template
from flowstat.model.events import Event, EventKind
from flowstat.model.states import TERMINAL_STEP_STATES, RunOutcome, StepState


@dataclass(frozen=True, slots=True)
class Start:
    step: str
    action: str
    args: dict[str, object]
    attempt: int = 1  # 1 for the first call; a retry has the next number (it is part of the key)
    delay: float = 0.0  # seconds the driver waits before the call (the backoff of a retry)
    compensation: bool = False  # the call undoes a step that succeeded (dsl-spec 5.4)


@dataclass(frozen=True, slots=True)
class Cancel:
    step: str


Command = Start | Cancel

# Given the plan and the steps to undo, the steps that must be undone before each of them
# (recovery.saga.compensation_blockers). The engine receives it so that it does not import C4.
CompensationPlanner = Callable[[Plan, Collection[str]], Mapping[str, frozenset[str]]]

# What an undo gets when its step declares no retry: three retries, exponential, from 1 second.
DEFAULT_UNDO_RETRY = RetrySpec(3, "exponential", 1.0)

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
        policy: str = "longest_path",
        compensation_planner: CompensationPlanner | None = None,
    ) -> None:
        if max_parallel < 1:
            raise ValueError("max_parallel must be at least 1")
        if policy not in ("longest_path", "fifo"):
            raise ValueError(f"unknown policy {policy!r}: use longest_path or fifo")
        if compensation_planner is None and any(s.on_failure == "compensate" for s in plan.steps):
            raise ValueError(
                "a step uses on_failure: compensate, so a compensation planner is needed"
            )
        self._planner = compensation_planner
        self._fifo = policy == "fifo"
        self._arrivals = 0
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
        self._attempt = [1] * n  # the attempt that is running (or ran last) for each step
        self._args: list[dict[str, object]] = [{} for _ in range(n)]  # as rendered at dispatch
        self._commands: list[Command] = []
        self._terminal = 0
        self._started = False
        self._stopped = False
        self._cancelled = False
        self._compensate_requested = False  # a step failed under on_failure: compensate
        self._undoing = False  # the compensation phase has begun
        self._undo_blockers: dict[int, set[int]] = {}  # undo of i waits for these steps' undos
        self._undo_waiting_on: dict[int, set[int]] = {}  # undo of i unblocks these
        self._undo_ready: list[int] = []  # heap of -index: later steps are undone first
        self._undo_running: set[int] = set()
        self._undo_failed = False
        self._uncertain: set[int] = set()  # steps whose outcome a crash made unknown (US-16)
        self._uncertain_undo: set[int] = set()  # those of them that were undoing a step
        self._ctx = EvalContext(self.inputs, self.outputs, self._instances)

    # -- what the driver may ask ---------------------------------------------------------------
    @property
    def status(self) -> str:
        if self.outcome is not None:
            return "FINISHED"
        if not self._started:
            return "CREATED"
        if self._undoing:
            return "WAITING" if self._uncertain and not self._undo_running else "COMPENSATING"
        if self._running or self._ready or self._queue:
            return "RUNNING"
        return "WAITING" if self._waiting or self._uncertain else "RUNNING"

    def waiting_steps(self) -> list[str]:
        """Steps that wait for an answer, in the order of the plan."""
        return [s.id for s in self.plan.steps if self.states[s.id] is StepState.WAITING]

    def arguments(self, step_id: str) -> dict[str, object]:
        """The arguments the step was given when it started (as recorded outputs render them)."""
        return dict(self._args[self._index[step_id]])

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
        if i is not None and self.states[step_id] is StepState.COMPENSATING:
            return self._undo_finished(i, now)
        if i is None or self.states[step_id] is not StepState.RUNNING:
            return []
        return self._complete(i, output, now)

    def signal(self, step_id: str, output: dict[str, object], now: float) -> list[Command]:
        """The answer a waiting step was paused for; ignored when the step is not waiting."""
        i = self._index.get(step_id)
        if i is None or self.states[step_id] is not StepState.WAITING:
            return []
        return self._complete(i, output, now)

    def step_failed(
        self, step_id: str, code: str, message: str, now: float, transient: bool = False
    ) -> list[Command]:
        """The action failed. A transient failure of a running step with a retry left gives the
        step another attempt (docs/dsl-spec.md 5.3); anything else finishes the step as FAILED."""
        i = self._index.get(step_id)
        if i is None:
            return []
        state = self.states[step_id]
        if state is StepState.COMPENSATING:
            undo_retry = self.plan.steps[i].retry or DEFAULT_UNDO_RETRY
            if transient and self._attempt[i] <= undo_retry.max:
                return [self._retry(i, undo_retry, code, message, now)]
            text = f"the undo failed: {code} {message}"
            return self._undo_finished(i, now, "E-COMP-001", text)
        if state not in (StepState.RUNNING, StepState.WAITING):
            return []
        retry = self.plan.steps[i].retry
        if (
            transient
            and retry is not None
            and state is StepState.RUNNING
            and self._attempt[i] <= retry.max
        ):
            return [self._retry(i, retry, code, message, now)]
        self._finish(i, StepState.FAILED, now, code, message)
        return self._advance(now)

    def _retry(self, i: int, retry: RetrySpec, code: str, message: str, now: float) -> Start:
        step = self.plan.steps[i]
        undo = self.states[step.id] is StepState.COMPENSATING
        retries_done = self._attempt[i]  # attempt 1 failed: this is retry number 1
        factor = 2 ** (retries_done - 1) if retry.backoff == "exponential" else 1
        delay = retry.delay * factor
        self._attempt[i] += 1
        self._log(
            EventKind.STEP_RETRY,
            now,
            step=step.id,
            code=code,
            message=message,
            attempt=self._attempt[i],
            delay=delay,
        )
        action = step.compensate if undo and step.compensate is not None else step.action
        return Start(step.id, action, dict(self._args[i]), self._attempt[i], delay, undo)

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
        """Stop the run. Ignored once it has finished or has begun to undo its work: an undo is
        never interrupted halfway, or the effects would be left in a state nobody recorded."""
        if self.outcome is not None or self._undoing:
            return []
        self._started = True
        self._cancelled = True
        self._stop(now, keep=-1)
        return self._advance(now)

    # -- recovery after a crash (docs/dsl-spec.md 5.5) ------------------------------------------
    def restore(
        self, events: Iterable[Event], now: float, idempotent: Callable[[str], bool]
    ) -> list[Command]:
        """Rebuild the run from its recorded history and return the commands that go on from it.

        The recorded states are the state; no decision is taken again. A step that was RUNNING
        (or whose undo was running) is run again with the same attempt number when its action is
        idempotent, and becomes UNCERTAIN otherwise. ``idempotent`` tells it for an action name;
        the engine does not know the registry of actions. Steps that were decided but had no event
        of their own are decided again, which gives the same answer: a decision is a function of
        recorded values.
        """
        if self._started or self.events:
            raise ValueError("restore needs a new scheduler")
        recorded = list(events)
        if not recorded:
            return self.start(now)
        self.events = recorded
        self._started = True
        ran = self._fold(recorded)
        if self.outcome is not None:
            return []
        self._log(EventKind.RUN_RESUMED, now)
        steps = self.plan.steps
        states = self.states
        self._terminal = sum(1 for s in steps if states[s.id] in TERMINAL_STEP_STATES)
        for j, preds in enumerate(self._preds):
            self._remaining[j] = sum(
                1 for p in preds if states[steps[p].id] not in TERMINAL_STEP_STATES
            )
        stopping = any(
            states[s.id] is StepState.FAILED and s.on_failure in ("fail", "compensate")
            for s in steps
        )
        cancelled = any(states[s.id] is StepState.CANCELLED for s in steps)
        self._compensate_requested = any(
            states[s.id] is StepState.FAILED and s.on_failure == "compensate" for s in steps
        )
        self._cancelled = cancelled and not stopping
        self._stopped = stopping or cancelled or self._undoing
        for i in ran:  # what a step was given is a function of the recorded outputs
            self._args[i] = self._render(i)
        if self._stopped and not self._undoing:
            self._stop(now, keep=-1)  # a stop that was recorded in part is completed
            self._commands.clear()  # nothing is running any more, so nothing is to be cancelled
        for i, step in enumerate(steps):
            if states[step.id] is StepState.RUNNING:
                self._recover(i, step.action, False, idempotent, now)
            elif states[step.id] is StepState.COMPENSATING and step.compensate is not None:
                self._recover(i, step.compensate, True, idempotent, now)
            elif states[step.id] is StepState.WAITING:
                self._waiting.add(i)
            elif states[step.id] is StepState.UNCERTAIN:
                self._uncertain.add(i)
        if self._undoing:
            self._rebuild_undo()
            self._undo_dispatch(now)
            self._undo_maybe_finish(now)
            return self._flush()
        self._queue.extend(
            i
            for i, s in enumerate(steps)
            if states[s.id] is StepState.PENDING and self._remaining[i] == 0
        )
        return self._advance(now)

    def _fold(self, recorded: list[Event]) -> set[int]:
        """Apply the recorded events to the states. Returns the steps that have run."""
        ran: set[int] = set()
        undo_uncertain: set[int] = set()
        for e in recorded:
            i = self._index.get(e.step) if e.step is not None else None
            if e.step is not None and i is None:
                raise ValueError(f"unknown step {e.step!r}: the history is of another workflow")
            if e.kind is EventKind.RUN_FINISHED:
                self.outcome = e.outcome
            elif e.kind is EventKind.COMPENSATION_STARTED:
                self._undoing = True
            elif i is None:
                continue
            elif e.kind is EventKind.STEP_STARTED:
                self.states[e.step or ""] = StepState.RUNNING
                self._attempt[i] = 1
                ran.add(i)
            elif e.kind is EventKind.STEP_WAITING:
                self.states[e.step or ""] = StepState.WAITING
            elif e.kind is EventKind.STEP_RETRY and e.attempt is not None:
                self._attempt[i] = e.attempt
            elif e.kind is EventKind.STEP_COMPENSATING:
                self.states[e.step or ""] = StepState.COMPENSATING
                self._attempt[i] = e.attempt or 1
                ran.add(i)
            elif e.kind is EventKind.STEP_UNCERTAIN:
                self.states[e.step or ""] = StepState.UNCERTAIN
                self._attempt[i] = e.attempt or self._attempt[i]
                if e.state is StepState.COMPENSATING:
                    undo_uncertain.add(i)
            elif e.kind is EventKind.STEP_RESOLVED:
                if e.decision == "retry":
                    undo = i in undo_uncertain
                    self.states[e.step or ""] = (
                        StepState.COMPENSATING if undo else StepState.RUNNING
                    )
                    self._attempt[i] = e.attempt or self._attempt[i] + 1
                undo_uncertain.discard(i)
            elif e.kind is EventKind.STEP_FINISHED and e.state is not None:
                self.states[e.step or ""] = e.state
                if e.state is StepState.SUCCEEDED:
                    self.outputs[e.step or ""] = dict(e.output or {})
                if e.code is not None:
                    self.errors[e.step or ""] = (e.code, e.message or "")
        self._uncertain_undo = {
            i for i in undo_uncertain if self.states[self.plan.steps[i].id] is StepState.UNCERTAIN
        }
        return ran

    def _render(self, i: int) -> dict[str, object]:
        try:
            return {n: render_template(parts, self._ctx) for n, parts in self.plan.steps[i].with_}
        except EvalError:  # cannot happen for a step that ran: its inputs were recorded
            return {}

    def _recover(
        self, i: int, action: str, undo: bool, idempotent: Callable[[str], bool], now: float
    ) -> None:
        """A call was in flight at the crash: repeat it with the same key, or ask a person."""
        step = self.plan.steps[i]
        if idempotent(action):
            (self._undo_running if undo else self._running).add(i)
            start = Start(step.id, action, dict(self._args[i]), self._attempt[i], 0.0, undo)
            self._commands.append(start)
            return
        self.states[step.id] = StepState.UNCERTAIN
        self._uncertain.add(i)
        if undo:
            self._uncertain_undo.add(i)
        self._log(
            EventKind.STEP_UNCERTAIN,
            now,
            step=step.id,
            state=StepState.COMPENSATING if undo else StepState.RUNNING,
            code="E-REC-001",
            message=(
                f"the engine stopped during the call of {action}; the outcome is unknown "
                "(the effect may exist): decide with 'resolve' (done or retry)"
            ),
            attempt=self._attempt[i],
        )

    def _rebuild_undo(self) -> None:
        """The compensation phase as it stands after the recorded events."""
        assert self._planner is not None
        steps = self.plan.steps
        live = {
            StepState.SUCCEEDED,
            StepState.COMPENSATING,
            StepState.COMPENSATED,
            StepState.COMPENSATION_FAILED,
        }
        to_undo = [
            s.id
            for i, s in enumerate(steps)
            if s.compensate is not None and (self.states[s.id] in live or i in self._uncertain_undo)
        ]
        blockers = self._planner(self.plan, to_undo)
        for step_id in to_undo:
            i = self._index[step_id]
            if self.states[step_id] in (StepState.COMPENSATED, StepState.COMPENSATION_FAILED):
                self._undo_failed |= self.states[step_id] is StepState.COMPENSATION_FAILED
                continue
            waiting_for = {
                self._index[b]
                for b in blockers[step_id]
                if self.states[b] is not StepState.COMPENSATED
            }
            self._undo_blockers[i] = waiting_for
            for b in waiting_for:
                self._undo_waiting_on.setdefault(b, set()).add(i)
            if self.states[step_id] is StepState.SUCCEEDED and not waiting_for:
                heapq.heappush(self._undo_ready, -i)

    def resolve(
        self,
        step_id: str,
        decision: str,
        now: float,
        output: Mapping[str, object] | None = None,
    ) -> list[Command]:
        """A person decides about an UNCERTAIN step: ``done`` (the effect exists; ``output`` is
        what the step would have produced) or ``retry`` (a new attempt). Ignored when the step is
        not uncertain."""
        if decision not in ("done", "retry"):
            raise ValueError("the decision is done or retry")
        i = self._index.get(step_id)
        if i is None or self.states[step_id] is not StepState.UNCERTAIN:
            return []
        step = self.plan.steps[i]
        undo = i in self._uncertain_undo
        self._uncertain.discard(i)
        self._uncertain_undo.discard(i)
        if decision == "done":
            self._log(EventKind.STEP_RESOLVED, now, step=step_id, decision="done")
            if undo:
                return self._undo_finished(i, now)
            return self._complete(i, dict(output or {}), now)
        self._attempt[i] += 1
        self._log(
            EventKind.STEP_RESOLVED, now, step=step_id, decision="retry", attempt=self._attempt[i]
        )
        if undo:
            self.states[step_id] = StepState.COMPENSATING
            self._undo_running.add(i)
            action = step.compensate or step.action
        else:
            self.states[step_id] = StepState.RUNNING
            self._running.add(i)
            action = step.action
        self._commands.append(
            Start(step_id, action, dict(self._args[i]), self._attempt[i], 0.0, undo)
        )
        return self._flush()

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
        self._uncertain.discard(i)
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
        elif state is StepState.FAILED and step.on_failure == "compensate":
            self._compensate_requested = True
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
            self._uncertain.discard(j)
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
        if self._fifo:
            self._arrivals += 1
            heapq.heappush(self._ready, (float(self._arrivals), i))
        else:
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
        self._attempt[i] = 1
        self._args[i] = args
        self._log(EventKind.STEP_STARTED, now, step=step.id)
        self._commands.append(Start(step.id, step.action, dict(args)))

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
        if self._terminal < len(self.plan.steps) or self._undoing:
            return
        if self._compensate_requested and not self._cancelled:
            self._begin_undo(now)
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

    # -- compensation phase (docs/dsl-spec.md 5.4) ----------------------------------------------
    def _begin_undo(self, now: float) -> None:
        """The run has stopped: undo the steps that succeeded, later steps first."""
        assert self._planner is not None
        steps = self.plan.steps
        to_undo = [
            s.id
            for s in steps
            if self.states[s.id] is StepState.SUCCEEDED and s.compensate is not None
        ]
        blockers = self._planner(self.plan, to_undo)
        self._undoing = True
        self._log(EventKind.COMPENSATION_STARTED, now)
        for step_id in to_undo:
            i = self._index[step_id]
            self._undo_blockers[i] = {self._index[b] for b in blockers[step_id]}
            for b in self._undo_blockers[i]:
                self._undo_waiting_on.setdefault(b, set()).add(i)
        for i, waiting_for in self._undo_blockers.items():
            if not waiting_for:
                heapq.heappush(self._undo_ready, -i)
        self._undo_dispatch(now)
        self._undo_maybe_finish(now)

    def _undo_dispatch(self, now: float) -> None:
        while self._undo_ready and len(self._undo_running) < self.max_parallel:
            i = -heapq.heappop(self._undo_ready)
            step = self.plan.steps[i]
            assert step.compensate is not None
            self.states[step.id] = StepState.COMPENSATING
            self._undo_running.add(i)
            self._attempt[i] = 1
            self._log(EventKind.STEP_COMPENSATING, now, step=step.id, attempt=1)
            self._commands.append(
                Start(step.id, step.compensate, dict(self._args[i]), 1, 0.0, compensation=True)
            )

    def _undo_finished(
        self, i: int, now: float, code: str | None = None, message: str | None = None
    ) -> list[Command]:
        step = self.plan.steps[i]
        ok = code is None
        state = StepState.COMPENSATED if ok else StepState.COMPENSATION_FAILED
        self.states[step.id] = state
        self._undo_running.discard(i)
        if not ok:
            self._undo_failed = True
            self.errors[step.id] = (code or "", message or "")
        self._log(
            EventKind.STEP_FINISHED, now, step=step.id, state=state, code=code, message=message
        )
        if ok:
            for waiting in self._undo_waiting_on.get(i, ()):
                self._undo_blockers[waiting].discard(i)
                if not self._undo_blockers[waiting]:
                    heapq.heappush(self._undo_ready, -waiting)
        self._undo_dispatch(now)
        self._undo_maybe_finish(now)
        return self._flush()

    def _undo_maybe_finish(self, now: float) -> None:
        if self._undo_running or self._undo_ready or self._uncertain:
            return
        # steps whose undo was blocked by a failed undo stay SUCCEEDED: the run says so
        self.outcome = (
            RunOutcome.COMPENSATION_FAILED if self._undo_failed else RunOutcome.COMPENSATED
        )
        self._log(EventKind.RUN_FINISHED, now, outcome=self.outcome)
