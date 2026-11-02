"""Diagnostics: what the compiler tells the user about a document.

Every diagnostic has a stable code (see docs/dsl-spec.md, section 6), a severity, a message and a
position in the original file (1-based line and column).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    severity: Severity
    message: str
    line: int
    column: int
