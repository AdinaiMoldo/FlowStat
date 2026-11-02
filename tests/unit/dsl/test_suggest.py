"""Tests of the edit distance and the did-you-mean rule."""

from __future__ import annotations

import random

import pytest

from flowstat.dsl.suggest import levenshtein, suggest


@pytest.mark.parametrize(
    ("a", "b", "d"),
    [
        ("", "", 0),
        ("abc", "", 3),
        ("", "abc", 3),
        ("kitten", "sitting", 3),
        ("flaw", "lawn", 2),
        ("when", "wen", 1),
        ("same", "same", 0),
        ("ab", "ba", 2),
    ],
)
def test_levenshtein_known_values(a: str, b: str, d: int) -> None:
    assert levenshtein(a, b) == d


def test_levenshtein_is_symmetric_and_obeys_the_triangle_inequality() -> None:
    rng = random.Random(7)
    alphabet = "abc"
    for _ in range(500):
        x, y, z = (
            "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 6))) for _ in range(3)
        )
        assert levenshtein(x, y) == levenshtein(y, x)
        assert levenshtein(x, z) <= levenshtein(x, y) + levenshtein(y, z)


KEYS = ["action", "with", "needs", "join", "when", "ensures", "foreach", "retry", "compensate"]


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("wen", "when"),
        ("When", "when"),  # case only
        ("nedes", "needs"),
        ("actoin", "action"),
        ("foreech", "foreach"),
        ("compensat", "compensate"),
        ("zzzzzz", None),
        ("x", None),  # too short to guess
        ("", None),
    ],
)
def test_suggest(word: str, expected: str | None) -> None:
    assert suggest(word, KEYS) == expected


def test_suggest_prefers_the_closest_then_the_first_declared() -> None:
    assert suggest("joi", ["join", "joint"]) == "join"
    assert suggest("ab", ["abc", "abd"]) == "abc"


def test_suggest_never_returns_the_word_itself() -> None:
    assert suggest("when", KEYS) is None
