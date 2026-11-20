"""The registry: finds an action, checks its arguments and its declared output schema.

The workflow text does not repeat the schema of an action's output; the action declares it here
and the registry checks it when the action returns (docs/milestones/M2.md, audit point 4).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace

from flowstat.actions.base import ActionContext, ActionError, ActionSpec
from flowstat.actions.database import delete_batch, write_rows
from flowstat.actions.email import send_email
from flowstat.actions.http import calling as http_calling
from flowstat.actions.statguard import validate
from flowstat.actions.stats import aggregate
from flowstat.actions.xlsx import read_form
from flowstat.analysis.signatures import ActionSignature, Choices, Signatures
from flowstat.compiler.cache import PlanCache
from flowstat.compiler.plan import Plan
from flowstat.engine.runner import Error, Executor, Ok, Outcome, Pause
from flowstat.engine.scheduler import Start

_NOTATION = {str: "string", int: "int", float: "float", bool: "bool", list: "list<any>"}


def notation(python_type: type) -> str:
    """The type of the DSL that a Python class stands for (``any`` when there is none)."""
    return _NOTATION.get(python_type, "any")


def _declared(
    python: Mapping[str, type] | None, notated: Mapping[str, str] | None
) -> dict[str, str] | None:
    """The types of a group of names in the notation of the DSL; ``None`` when not declared."""
    if python is None and notated is None:
        return None
    merged = {name: notation(t) for name, t in (python or {}).items()}
    merged.update(notated or {})
    return merged


def _fits(value: object, expected: type) -> bool:
    if isinstance(value, bool):
        return expected is bool
    return isinstance(value, expected)


class Registry:
    def __init__(self, specs: Iterable[ActionSpec], plan_cache_size: int = 64) -> None:
        self._specs = {s.name: s for s in specs}
        # The plans compiled against this registry (its actions and their signatures), kept for
        # the process that holds it: the services that are built for a request take it from here.
        self.plans: PlanCache[Plan] = PlanCache(plan_cache_size)

    def names(self) -> frozenset[str]:
        return frozenset(self._specs)

    def signatures(self) -> Signatures:
        """What the analysis of guards may assume about the outputs (analysis/signatures.py)."""
        kinds = {int: "int", float: "real", bool: "bool", str: "str", list: "list"}
        out: dict[str, ActionSignature] = {}
        for name, spec in self._specs.items():
            fields = {f: kinds[t] for f, t in (spec.output or {}).items() if t in kinds}
            outputs = _declared(spec.output, spec.output_types)
            optional = dict(spec.optional or {})
            for choice in (spec.choices or {}).values():
                optional.setdefault(choice.argument, "list<string>")
            out[name] = ActionSignature(
                fields,
                dict(spec.choices or {}),
                spec.waits,
                _declared(spec.params, spec.param_types) or {},
                optional,
                outputs,
                spec.effect,
                spec.hosts,
            )
        return out

    def spec(self, action: str) -> ActionSpec | None:
        return self._specs.get(action)

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


def default_registry(hosts: frozenset[str] | None = None) -> Registry:
    """The actions of the engine. ``hosts`` is the list of hosts ``http.call`` may reach (US-39);
    None: any host."""
    rows = "list<row>"
    return Registry(
        [
            ActionSpec(
                "xlsx.read",
                {"path": str},
                {"rows": list, "count": int},
                read_form,
                idempotent=True,
                output_types={"rows": rows},
                repeatable=True,
                reads="path",
            ),
            ActionSpec(
                "statguard.validate",
                {"rows": list},
                {"rows": list, "checked": int},
                validate,
                idempotent=True,
                param_types={"rows": rows},
                optional={"rules": "string"},
                output_types={"rows": rows},
                repeatable=True,
            ),
            ActionSpec(
                "stats.aggregate",
                {"parts": list},
                {"rows": list, "regions": int},
                aggregate,
                idempotent=True,
                param_types={"parts": f"list<{rows}>"},
                output_types={"rows": rows},
                repeatable=True,
            ),
            ActionSpec(
                "approval.wait",
                {"role": str},
                None,
                lambda args, ctx: Pause(),
                idempotent=True,
                choices={"decision": Choices(("approved", "rejected"), "decisions")},
                waits=True,
                output_types={"decision": "string", "by": "string"},
            ),
            ActionSpec(
                "db.write",
                {"rows": list},
                {"written": int, "batch": str},
                write_rows,
                idempotent=True,
                param_types={"rows": rows},
                effect=True,
            ),
            ActionSpec("db.delete_batch", {}, {"deleted": int}, delete_batch, idempotent=True),
            ActionSpec(
                "http.call",
                {"url": str},
                {"status": int},
                http_calling(hosts),
                idempotent=True,
                optional={"method": "string", "body": "any", "timeout": "float"},
                effect=True,
                hosts=hosts,
            ),
            # the outbox keeps one row per key, but a real mail server would send twice
            ActionSpec(
                "email.send", {}, {"queued": int}, send_email, idempotent=False, effect=True
            ),
        ]
    )
