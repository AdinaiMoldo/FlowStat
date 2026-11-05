"""The registry: finds an action, checks its arguments and its declared output schema.

The workflow text does not repeat the schema of an action's output; the action declares it here
and the registry checks it when the action returns (docs/milestones/M2.md, audit point 4).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from flowstat.actions.base import ActionContext, ActionError, ActionSpec
from flowstat.actions.database import delete_batch, write_rows
from flowstat.actions.email import send_email
from flowstat.actions.http import call as http_call
from flowstat.actions.statguard import validate
from flowstat.actions.stats import aggregate
from flowstat.actions.xlsx import read_form
from flowstat.analysis.signatures import ActionSignature, Choices, Signatures
from flowstat.engine.runner import Error, Executor, Ok, Outcome, Pause
from flowstat.engine.scheduler import Start


def _fits(value: object, expected: type) -> bool:
    if isinstance(value, bool):
        return expected is bool
    return isinstance(value, expected)


class Registry:
    def __init__(self, specs: Iterable[ActionSpec]) -> None:
        self._specs = {s.name: s for s in specs}

    def names(self) -> frozenset[str]:
        return frozenset(self._specs)

    def signatures(self) -> Signatures:
        """What the analysis of guards may assume about the outputs (analysis/signatures.py)."""
        kinds = {int: "int", float: "real", bool: "bool", str: "str"}
        out: dict[str, ActionSignature] = {}
        for name, spec in self._specs.items():
            fields = {f: kinds[t] for f, t in (spec.output or {}).items() if t in kinds}
            out[name] = ActionSignature(fields, dict(spec.choices or {}))
        return out

    def is_idempotent(self, action: str) -> bool:
        """Whether the engine may repeat a call of this action after a crash (unknown: no)."""
        spec = self._specs.get(action)
        return spec is not None and spec.idempotent

    def executor(self, base: ActionContext) -> Executor:
        """An executor for the thread-pool driver; ``base`` gives the run, the step is added."""

        def execute(command: Start) -> Outcome:
            spec = self._specs.get(command.action)
            if spec is None:
                return Error("E-ACT-001", f"unknown action {command.action}")
            for name, expected in spec.params.items():
                if name not in command.args:
                    return Error("E-ACT-009", f"{spec.name} needs the argument '{name}'")
                if not _fits(command.args[name], expected):
                    return Error(
                        "E-ACT-009",
                        f"{spec.name}: argument '{name}' must be {expected.__name__}, "
                        f"not {type(command.args[name]).__name__}",
                    )
            try:
                result = spec.handler(
                    command.args,
                    replace(
                        base,
                        step=command.step,
                        attempt=command.attempt,
                        compensation=command.compensation,
                    ),
                )
            except ActionError as error:
                return Error(error.code, error.message, error.transient)
            if isinstance(result, Pause):
                return result
            if spec.output is not None:
                for name, expected in spec.output.items():
                    if name not in result or not _fits(result[name], expected):
                        return Error(
                            "E-ACT-004",
                            f"the output of {spec.name} breaks its declared schema at '{name}'",
                        )
            return Ok(result)

        return execute


def default_registry() -> Registry:
    return Registry(
        [
            ActionSpec(
                "xlsx.read", {"path": str}, {"rows": list, "count": int}, read_form, idempotent=True
            ),
            ActionSpec(
                "statguard.validate",
                {"rows": list},
                {"rows": list, "checked": int},
                validate,
                idempotent=True,
            ),
            ActionSpec(
                "stats.aggregate",
                {"parts": list},
                {"rows": list, "regions": int},
                aggregate,
                idempotent=True,
            ),
            ActionSpec(
                "approval.wait",
                {"role": str},
                None,
                lambda args, ctx: Pause(),
                idempotent=True,
                choices={"decision": Choices(("approved", "rejected"), "decisions")},
            ),
            ActionSpec(
                "db.write",
                {"rows": list},
                {"written": int, "batch": str},
                write_rows,
                idempotent=True,
            ),
            ActionSpec("db.delete_batch", {}, {"deleted": int}, delete_batch, idempotent=True),
            ActionSpec("http.call", {"url": str}, {"status": int}, http_call, idempotent=True),
            # the outbox keeps one row per key, but a real mail server would send twice
            ActionSpec("email.send", {}, {"queued": int}, send_email, idempotent=False),
        ]
    )
