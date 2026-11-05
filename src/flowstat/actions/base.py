"""What every step action shares: the context it runs in, its errors, its declared interface."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from flowstat.engine.runner import Pause


class ActionError(Exception):
    """The step fails. ``code`` is an E-ACT code of docs/dsl-spec.md, ``message`` is for people."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code} {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ActionContext:
    run_id: str  # the batch of a run: what db.write stores and db.delete_batch removes
    step: str
    data_dir: Path  # root of the regional forms; actions never read outside of it
    target: str | None  # path of the database the results are written to


Args = Mapping[str, object]
Handler = Callable[[Args, ActionContext], Mapping[str, object] | Pause]


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """An action with its declared interface, checked by the registry on every call."""

    name: str
    params: Mapping[str, type]  # required arguments and their types
    output: Mapping[str, type] | None  # declared fields of the output; None: not checked
    handler: Handler
