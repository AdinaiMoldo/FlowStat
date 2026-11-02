"""Edit distance and the did-you-mean rule used by the shape validator."""

from __future__ import annotations

from collections.abc import Iterable

MAX_DISTANCE = 2


def levenshtein(a: str, b: str) -> int:
    """Minimum number of single-character insertions, deletions and substitutions."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def suggest(word: str, candidates: Iterable[str]) -> str | None:
    """The closest candidate within MAX_DISTANCE, ignoring case, or None.

    The distance must also be smaller than the length of the word, so that a one-letter word is
    never "corrected" to something unrelated. The first candidate wins a tie.
    """
    best: str | None = None
    best_distance = MAX_DISTANCE + 1
    lowered = word.lower()
    for candidate in candidates:
        if candidate == word:
            continue
        distance = levenshtein(lowered, candidate.lower())
        if distance <= MAX_DISTANCE and distance < len(word) and distance < best_distance:
            best, best_distance = candidate, distance
    return best
