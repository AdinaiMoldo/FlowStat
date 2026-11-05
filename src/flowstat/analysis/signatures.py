"""What the analysis may assume about the data that steps produce (C6).

An action declares the type of the fields of its output; a few fields are a choice from a list
(the ``decision`` of an approval), and a step may narrow or widen that list with an argument. The
analysis reads a guard such as ``${approve.decision} == 'approved'`` with these domains. A field
that is not described here is opaque to the analysis.
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


Signatures = Mapping[str, ActionSignature]
