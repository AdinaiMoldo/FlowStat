"""Catalogue snapshot (A4): named lists of items, for example the regional offices.

Every item is a flat mapping of text attributes and has a unique ``code``; a ``foreach`` step is
expanded once per item, and the code names the instance (``load[BIS]``).
"""

from __future__ import annotations

from dataclasses import dataclass

from flowstat.dsl.tree import Mapping, Scalar, Sequence, YNode
from flowstat.model.diagnostics import Diagnostic, Severity


@dataclass(frozen=True)
class Catalogue:
    lists: dict[str, tuple[dict[str, str], ...]]


def _error(code: str, message: str, node: YNode | Scalar) -> Diagnostic:
    return Diagnostic(code, Severity.ERROR, message, node.line, node.column)


def load_catalogue(root: YNode) -> tuple[Catalogue, list[Diagnostic]]:
    diags: list[Diagnostic] = []
    lists: dict[str, tuple[dict[str, str], ...]] = {}
    if not isinstance(root, Mapping):
        return Catalogue({}), [
            _error("E-CAT-001", "the catalogue must be a mapping of lists", root)
        ]
    for entry in root.entries:
        name = str(entry.key.value)
        if not isinstance(entry.value, Sequence):
            diags.append(_error("E-CAT-001", f"'{name}' must be a list of items", entry.value))
            continue
        items: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in entry.value.items:
            if not isinstance(item, Mapping):
                diags.append(_error("E-CAT-002", "an item must be a mapping", item))
                continue
            attrs = {
                str(e.key.value): str(e.value.value)
                for e in item.entries
                if isinstance(e.value, Scalar)
            }
            code = attrs.get("code")
            if code is None:
                diags.append(_error("E-CAT-003", "an item needs a 'code'", item))
            elif code in seen:
                diags.append(_error("E-CAT-004", f"duplicate code '{code}'", item))
            else:
                seen.add(code)
                items.append(attrs)
        lists[name] = tuple(items)
    return Catalogue(lists), diags
