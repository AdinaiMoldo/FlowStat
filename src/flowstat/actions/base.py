"""What every step action shares: the context it runs in, its errors, its declared interface."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from flowstat.analysis.signatures import Choices
from flowstat.engine.runner import Pause


class ActionError(Exception):
    """The step fails. ``code`` is an E-ACT code of docs/dsl-spec.md, ``message`` is for people."""

    def __init__(self, code: str, message: str, transient: bool = False) -> None:
        super().__init__(f"{code} {message}")
        self.code = code
        self.message = message
        self.transient = transient  # a retry may help (a lost connection), not a bad input


@dataclass(frozen=True, slots=True)
class ActionContext:
    run_id: str  # the batch of a run: what db.write stores and db.delete_batch removes
    step: str
    data_dir: Path  # root of the regional forms; actions never read outside of it
    target: str | None  # path of the database the results are written to
    attempt: int = 1  # which attempt of the step this is
    compensation: bool = False  # the call undoes the step instead of doing it

    @property
    def key(self) -> str:
        """Idempotency key: the same for a repeat of the same attempt, new for a new attempt."""
        kind = "compensate/" if self.compensation else ""
        return f"{self.run_id}/{self.step}/{kind}{self.attempt}"

    @property
    def operation_key(self) -> str:
        """The key of the operation: the same for every attempt of the step (or of its undo).

        For a receiver outside the engine. A retry after a lost answer must be recognised as the
        same operation, or the effect that the lost answer was about would be made twice; so this
        key has no attempt number. The attempt key above is what the engine records.
        """
        kind = "/compensate" if self.compensation else ""
        return f"{self.run_id}/{self.step}{kind}"


Args = Mapping[str, object]
Handler = Callable[[Args, ActionContext], Mapping[str, object] | Pause]


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """An action with its declared interface, checked by the registry on every call."""

    name: str
    params: Mapping[str, type]  # required arguments and their types
    output: Mapping[str, type] | None  # declared fields of the output; None: not checked
    handler: Handler
    # A repeated call with the same key has no further effect (docs/dsl-spec.md 5.5). The safe
    # default is False: after a crash such a call is not repeated by the engine, a person decides.
    idempotent: bool = False
    # Output fields that hold one of a list of strings (the decision of an approval); the analysis
    # of guards reads them as an enumeration. Not checked when the action returns (a person
    # answers an approval, so the answer comes from outside).
    choices: Mapping[str, Choices] | None = None
    waits: bool = False  # the action pauses the step until a person answers (an approval)
