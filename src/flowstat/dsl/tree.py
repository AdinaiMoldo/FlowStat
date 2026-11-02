"""Tree of a YAML document with positions, independent of the YAML library.

The reader (B1) builds it; the shape validator and the compiler read it. Lines and columns are
1-based and point into the original file.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Scalar:
    line: int
    column: int
    value: object  # str, int, float, bool or None


@dataclass(frozen=True, slots=True)
class Entry:
    key: Scalar
    value: YNode


@dataclass(frozen=True, slots=True)
class Mapping:
    line: int
    column: int
    entries: tuple[Entry, ...]

    def get(self, name: str) -> YNode | None:
        for entry in self.entries:
            if entry.key.value == name:
                return entry.value
        return None


@dataclass(frozen=True, slots=True)
class Sequence:
    line: int
    column: int
    items: tuple[YNode, ...]


YNode = Scalar | Mapping | Sequence
