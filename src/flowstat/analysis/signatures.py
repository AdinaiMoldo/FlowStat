"""What the analysis may assume about the actions and the data that steps produce (C6, C1, C2).

An action declares the type of the fields of its output; a few fields are a choice from a list
(the ``decision`` of an approval), and a step may narrow or widen that list with an argument. The
analysis reads a guard such as ``${approve.decision} == 'approved'`` with these domains. A field
that is not described here is opaque to the analysis. The checker of types (``types.py``) also
reads the arguments the action takes and the types of its output in the notation of the DSL
(docs/dsl-spec.md, section 4).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Choices:
    """A field that holds one of a list of strings."""

    default: tuple[str, ...]  # the values when the step gives none
    argument: str  # the argument of the step (in ``with``) that gives them


@dataclass(frozen=True, slots=True)
class ActionSignature:
    fields: Mapping[str, str]  # output field -> int, real, bool, str or list
    choices: Mapping[str, Choices] = field(default_factory=dict)
    waits: bool = False  # the step waits for an answer from a person (an approval)
    params: Mapping[str, str] = field(default_factory=dict)  # required arguments -> type
    optional: Mapping[str, str] = field(default_factory=dict)  # optional arguments -> type
    outputs: Mapping[str, str] | None = None  # field -> type; None: the output is not declared
    effect: bool = False  # the action changes something outside the engine
    hosts: frozenset[str] | None = None  # hosts a constant ``url`` may name; None: any (US-39)


Signatures = Mapping[str, ActionSignature]
