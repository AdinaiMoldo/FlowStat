"""Tests of the expression lexer (C1, level 1). Written before the lexer."""

from __future__ import annotations

import random

import pytest

from flowstat.dsl.lexer import Token, TokenKind, lex
from flowstat.model.diagnostics import Diagnostic

K = TokenKind


def kinds(text: str) -> list[TokenKind]:
    tokens, _ = lex(text)
    return [t.kind for t in tokens]


def texts(text: str) -> list[str]:
    tokens, _ = lex(text)
    return [t.text for t in tokens if t.kind is not K.EOF]


def codes(diags: list[Diagnostic]) -> list[str]:
    return [d.code for d in diags]


def test_reference_and_comparison() -> None:
    tokens, diags = lex("${approve.decision} == 'approved'")
    assert diags == []
    assert [t.kind for t in tokens] == [
        K.REF_OPEN, K.IDENT, K.DOT, K.IDENT, K.RBRACE, K.EQ, K.STRING, K.EOF,
    ]  # fmt: skip
    assert tokens[6].value == "approved"


def test_numbers() -> None:
    tokens, diags = lex("12 3.5 0")
    assert diags == []
    assert [(t.kind, t.value) for t in tokens[:3]] == [
        (K.INT, 12), (K.FLOAT, 3.5), (K.INT, 0),
    ]  # fmt: skip


def test_member_access_is_not_a_float() -> None:
    assert kinds("a.b") == [K.IDENT, K.DOT, K.IDENT, K.EOF]
    assert kinds("a[0].b") == [K.IDENT, K.LBRACKET, K.INT, K.RBRACKET, K.DOT, K.IDENT, K.EOF]


def test_keywords_are_case_sensitive() -> None:
    assert kinds("and or not in true false null") == [
        K.AND, K.OR, K.NOT, K.IN, K.TRUE, K.FALSE, K.NULL, K.EOF,
    ]  # fmt: skip
    assert kinds("And OR") == [K.IDENT, K.IDENT, K.EOF]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("== != <= >= < >", [K.EQ, K.NE, K.LE, K.GE, K.LT, K.GT]),
        ("+ - * / %", [K.PLUS, K.MINUS, K.STAR, K.SLASH, K.PERCENT]),
        ("( ) [ ] , .", [K.LPAREN, K.RPAREN, K.LBRACKET, K.RBRACKET, K.COMMA, K.DOT]),
    ],
)
def test_operators_and_punctuation(text: str, expected: list[TokenKind]) -> None:
    assert kinds(text) == [*expected, K.EOF]


def test_operators_without_spaces() -> None:
    assert kinds("a<=b!=c") == [K.IDENT, K.LE, K.IDENT, K.NE, K.IDENT, K.EOF]


def test_strings_with_both_quotes_and_escapes() -> None:
    tokens, diags = lex(r"""'it\'s' "say \"hi\"" 'a\nb' 'back\\slash'""")
    assert diags == []
    assert [t.value for t in tokens[:4]] == ["it's", 'say "hi"', "a\nb", "back\\slash"]


def test_invalid_escape_is_reported_and_lexing_goes_on() -> None:
    tokens, diags = lex(r"'a\qb' + 1")
    assert codes(diags) == ["E-EXPR-004"]
    assert (diags[0].line, diags[0].column) == (1, 3)
    assert [t.kind for t in tokens] == [K.STRING, K.PLUS, K.INT, K.EOF]


def test_unterminated_string_points_at_the_opening_quote() -> None:
    tokens, diags = lex("a == 'oops")
    assert codes(diags) == ["E-EXPR-002"]
    assert (diags[0].line, diags[0].column) == (1, 6)
    assert tokens[-1].kind is K.EOF


def test_unterminated_reference_is_reported_at_its_opening() -> None:
    _, diags = lex("len(${consolidate.rows")
    assert codes(diags) == ["E-EXPR-002"]
    assert (diags[0].line, diags[0].column) == (1, 5)


def test_closed_reference_is_not_reported() -> None:
    _, diags = lex("len(${consolidate.rows})")
    assert diags == []


def test_unexpected_character_gives_an_error_token_and_continues() -> None:
    tokens, diags = lex("a @ b")
    assert codes(diags) == ["E-EXPR-005"]
    assert [t.kind for t in tokens] == [K.IDENT, K.ERROR, K.IDENT, K.EOF]


def test_single_equals_suggests_double_equals() -> None:
    _, diags = lex("a = 1")
    assert codes(diags) == ["E-EXPR-005"]
    assert "'=='" in diags[0].message


def test_positions_are_one_based_and_follow_newlines() -> None:
    tokens, _ = lex("a\n  b")
    assert (tokens[0].line, tokens[0].column) == (1, 1)
    assert (tokens[1].line, tokens[1].column) == (2, 3)


def test_base_position_maps_tokens_to_the_original_file() -> None:
    """An expression starts at line 12, column 9 of the YAML file."""
    tokens, diags = lex("a + @", line=12, column=9)
    assert (tokens[0].line, tokens[0].column) == (12, 9)
    assert (tokens[2].line, tokens[2].column) == (12, 13)
    assert (diags[0].line, diags[0].column) == (12, 13)


def test_base_column_applies_only_to_the_first_line() -> None:
    tokens, _ = lex("a\nb", line=5, column=20)
    assert (tokens[1].line, tokens[1].column) == (6, 1)


def test_empty_and_blank_input_give_only_eof() -> None:
    assert kinds("") == [K.EOF]
    assert kinds("  \n\t ") == [K.EOF]


def test_texts_are_the_exact_source_slices() -> None:
    assert texts("a<=1.50") == ["a", "<=", "1.50"]


def _random_text(rng: random.Random) -> str:
    alphabet = "ab1.5 \n\t'\"\\${}()[],.+-*/%<>=!@_#&|~`;:?"
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))


def test_property_lexer_never_fails_and_tokens_cover_the_source_in_order() -> None:
    seed = 20261103
    rng = random.Random(seed)
    for _ in range(2000):
        text = _random_text(rng)
        tokens, _diags = lex(text)  # must not raise
        assert tokens[-1].kind is K.EOF, f"seed={seed} text={text!r}"
        offsets = [t.offset for t in tokens]
        assert offsets == sorted(offsets), f"seed={seed} text={text!r}"
        for t in _non_eof(tokens):
            assert text[t.offset : t.offset + len(t.text)] == t.text, f"seed={seed} {text!r}"


def _non_eof(tokens: list[Token]) -> list[Token]:
    return [t for t in tokens if t.kind is not K.EOF]
