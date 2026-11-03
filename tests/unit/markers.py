"""Helper for golden tests: markers ``⟦`` in a text mark the positions of expected diagnostics."""

from __future__ import annotations

MARK = "⟦"


def split_markers(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Remove the markers and return the clean text and the (line, column) of every marker."""
    clean: list[str] = []
    positions: list[tuple[int, int]] = []
    line, col = 1, 1
    for ch in text:
        if ch == MARK:
            positions.append((line, col))
            continue
        clean.append(ch)
        if ch == "\n":
            line, col = line + 1, 1
        else:
            col += 1
    return "".join(clean), positions
