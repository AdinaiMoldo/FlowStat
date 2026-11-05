"""Run the generated cases with mocked actions and compare with the prediction (US-24).

Every action returns an output made from its declared types (``0``, ``0.0``, ``false``, ``""``,
an empty list) with the fields that guards read set by the case; an approval waits and is answered
with the case's decision. The run is the engine's own scheduler on a virtual clock, so the states
are those of a real run. The prediction (``analysis/predict.py``) is a separate evaluation of the
guards and the join rules; a step that the two disagree about is reported, which is how a mistake
of either one, or a contract that the mocked output breaks, comes to light.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from flowstat.analysis.generate import Case, CaseSet
from flowstat.analysis.predict import RUN, SKIP, UNKNOWN, predict
from flowstat.analysis.signatures import ActionSignature, Signatures
from flowstat.analysis.variables import lower_guards
from flowstat.compiler.plan import Plan
from flowstat.engine.scheduler import Start
from flowstat.engine.simulation import Done, Result, Wait, simulate
from flowstat.model.states import StepState
from flowstat.recovery.saga import compensation_blockers

_DEFAULTS: dict[str, object] = {"int": 0, "real": 0.0, "bool": False, "str": "", "list": []}


@dataclass(frozen=True, slots=True)
class Difference:
    case: int  # 1-based
    step: str
    predicted: str  # run or skip
    observed: str  # run, skip, or the state the engine reached (FAILED, ...)


@dataclass(frozen=True, slots=True)
class CaseRun:
    index: int  # 1-based
    case: Case
    predicted: dict[str, str]
    observed: dict[str, str]
    differences: tuple[Difference, ...]


def run_cases(
    plan: Plan, cases: CaseSet, inputs: Mapping[str, object], signatures: Signatures
) -> list[CaseRun]:
    """Run each case; ``inputs`` are the values for the inputs that no guard reads."""
    preds = lower_guards(plan, signatures).preds
    runs: list[CaseRun] = []
    for index, case in enumerate(cases.cases, start=1):
        merged = {**inputs, **case.inputs}
        outputs = {
            s.id: _output(signatures.get(s.action), case.outputs.get(s.id)) for s in plan.steps
        }
        waiting = {s.id for s in plan.steps if signatures.get(s.action, _NONE).waits}

        def behavior(
            command: Start,
            outputs: dict[str, dict[str, object]] = outputs,
            waiting: set[str] = waiting,
        ) -> Result:
            return Wait() if command.step in waiting else Done(1.0, outputs[command.step])

        answers = {step: (1.0, outputs[step]) for step in waiting}
        sim = simulate(
            plan,
            merged,
            max_parallel=4,
            behavior=behavior,
            answers=answers,
            compensation_planner=compensation_blockers,
        )
        observed = {s: _label(state) for s, state in sim.scheduler.states.items()}
        values: dict[str, object] = {f"inputs.{k}": v for k, v in merged.items()}
        for step, fields in outputs.items():
            values.update({f"{step}.{name}": v for name, v in fields.items()})
        predicted = predict(plan, preds, values)
        differences = tuple(
            Difference(index, step, label, observed[step])
            for step, label in predicted.items()
            if label != UNKNOWN and observed[step] != label
        )
        runs.append(CaseRun(index, case, predicted, observed, differences))
    return runs


_NONE = ActionSignature({})


def _output(
    signature: ActionSignature | None, given: Mapping[str, object] | None
) -> dict[str, object]:
    out: dict[str, object] = {}
    if signature is not None:
        out = {name: _copy(_DEFAULTS[kind]) for name, kind in signature.fields.items()}
    out.update(given or {})
    return out


def _copy(value: object) -> object:
    return list(value) if isinstance(value, list) else value


def _label(state: StepState) -> str:
    if state is StepState.SUCCEEDED:
        return RUN
    if state is StepState.SKIPPED:
        return SKIP
    return str(state.value)
