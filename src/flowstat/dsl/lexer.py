"""Expression lexer (C1, level 1).

Turns the text of an expression (the content of ``when`` and ``ensures``) into tokens with
positions. It never raises: a problem becomes a diagnostic and an ERROR token, and lexing goes on,
so one run reports every lexical error. Positions are mapped to the original YAML file through the
``line`` and ``column`` of the first character of the text.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from flowstat.model.diagnostics import Diagnostic, Severity


class TokenKind(Enum):
    INT = auto()
    FLOAT = auto()
    STRING = auto()
    IDENT = auto()
    AND = auto()
    OR = auto()
    NOT = auto()
    IN = auto()
    TRUE = auto()
    FALSE = auto()
    NULL = auto()
    EQ = auto()
    NE = auto()
    LE = auto()
    GE = auto()
    LT = auto()
    GT = auto()
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    SLASH = auto()
    PERCENT = auto()
    LPAREN = auto()
    RPAREN = auto()
    LBRACKET = auto()
    RBRACKET = auto()
    COMMA = auto()
    DOT = auto()
    REF_OPEN = auto()  # ${
    RBRACE = auto()  # }
    ERROR = auto()
    EOF = auto()


@dataclass(frozen=True, slots=True)
class Token:
    kind: TokenKind
    text: str  # the exact slice of the source
    value: object  # int, float, str (decoded), bool, None
    offset: int  # index into the source text
    line: int
    column: int


_KEYWORDS: dict[str, tuple[TokenKind, object]] = {
    "and": (TokenKind.AND, None),
    "or": (TokenKind.OR, None),
    "not": (TokenKind.NOT, None),
    "in": (TokenKind.IN, None),
    "true": (TokenKind.TRUE, True),
    "false": (TokenKind.FALSE, False),
    "null": (TokenKind.NULL, None),
}

_TWO_CHAR: dict[str, TokenKind] = {
    "==": TokenKind.EQ,
    "!=": TokenKind.NE,
    "<=": TokenKind.LE,
    ">=": TokenKind.GE,
}

_ONE_CHAR: dict[str, TokenKind] = {
    "<": TokenKind.LT,
    ">": TokenKind.GT,
    "+": TokenKind.PLUS,
    "-": TokenKind.MINUS,
    "*": TokenKind.STAR,
    "/": TokenKind.SLASH,
    "%": TokenKind.PERCENT,
    "(": TokenKind.LPAREN,
    ")": TokenKind.RPAREN,
    "[": TokenKind.LBRACKET,
    "]": TokenKind.RBRACKET,
    ",": TokenKind.COMMA,
    ".": TokenKind.DOT,
    "}": TokenKind.RBRACE,
}

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"'}

_DIGITS = "0123456789"
_IDENT_START = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"
_IDENT_PART = _IDENT_START + _DIGITS
_SPACE = " \t\r\n"


class _Lexer:
    def __init__(self, text: str, line: int, column: int) -> None:
        self.text = text
        self.pos = 0
        self.line = line
        self.col = column
        self.tokens: list[Token] = []
        self.diags: list[Diagnostic] = []
        self.open_refs: list[tuple[int, int]] = []

    # -- position handling -------------------------------------------------
    def _advance(self, count: int) -> None:
        for _ in range(count):
            if self.text[self.pos] == "\n":
                self.line += 1
                self.col = 1
            else:
                self.col += 1
            self.pos += 1

    def _error(self, code: str, message: str, line: int, column: int) -> None:
        self.diags.append(Diagnostic(code, Severity.ERROR, message, line, column))

    def _emit(self, kind: TokenKind, start: int, line: int, col: int, value: object = None) -> None:
        self.tokens.append(Token(kind, self.text[start : self.pos], value, start, line, col))

    # -- main loop ---------------------------------------------------------
    def run(self) -> tuple[list[Token], list[Diagnostic]]:
        text = self.text
        while self.pos < len(text):
            ch = text[self.pos]
            if ch in _SPACE:
                self._advance(1)
            elif ch in _IDENT_START:
                self._ident()
            elif ch in _DIGITS:
                self._number()
            elif ch in "'\"":
                self._string(ch)
            elif text.startswith("${", self.pos):
                self._ref_open()
            elif text[self.pos : self.pos + 2] in _TWO_CHAR:
                self._simple(2, _TWO_CHAR[text[self.pos : self.pos + 2]])
            elif ch in _ONE_CHAR:
                if ch == "}" and self.open_refs:
                    self.open_refs.pop()
                self._simple(1, _ONE_CHAR[ch])
            else:
                self._unexpected(ch)
        for line, column in self.open_refs:
            self._error("E-EXPR-002", "unterminated reference: '${' is never closed", line, column)
        self.tokens.append(Token(TokenKind.EOF, "", None, len(text), self.line, self.col))
        return self.tokens, self.diags

    def _simple(self, width: int, kind: TokenKind) -> None:
        start, line, col = self.pos, self.line, self.col
        self._advance(width)
        self._emit(kind, start, line, col)

    def _ref_open(self) -> None:
        start, line, col = self.pos, self.line, self.col
        self.open_refs.append((line, col))
        self._advance(2)
        self._emit(TokenKind.REF_OPEN, start, line, col)

    def _ident(self) -> None:
        start, line, col = self.pos, self.line, self.col
        end = start + 1
        while end < len(self.text) and self.text[end] in _IDENT_PART:
            end += 1
        self._advance(end - start)
        word = self.text[start:end]
        if word in _KEYWORDS:
            kind, value = _KEYWORDS[word]
            self._emit(kind, start, line, col, value)
        else:
            self._emit(TokenKind.IDENT, start, line, col, word)

    def _number(self) -> None:
        start, line, col = self.pos, self.line, self.col
        end = start
        while end < len(self.text) and self.text[end] in _DIGITS:
            end += 1
        is_float = (
            end + 1 < len(self.text) and self.text[end] == "." and self.text[end + 1] in _DIGITS
        )
        if is_float:
            end += 1
            while end < len(self.text) and self.text[end] in _DIGITS:
                end += 1
        self._advance(end - start)
        literal = self.text[start:end]
        if is_float:
            self._emit(TokenKind.FLOAT, start, line, col, float(literal))
        else:
            self._emit(TokenKind.INT, start, line, col, int(literal))

    def _string(self, quote: str) -> None:
        start, line, col = self.pos, self.line, self.col
        self._advance(1)
        chars: list[str] = []
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            if ch == quote:
                self._advance(1)
                self._emit(TokenKind.STRING, start, line, col, "".join(chars))
                return
            if ch == "\\" and self.pos + 1 < len(self.text):
                esc_line, esc_col = self.line, self.col
                nxt = self.text[self.pos + 1]
                if nxt in _ESCAPES:
                    chars.append(_ESCAPES[nxt])
                else:
                    self._error(
                        "E-EXPR-004", f"invalid escape sequence '\\{nxt}'", esc_line, esc_col
                    )
                    chars.append(nxt)
                self._advance(2)
                continue
            chars.append(ch)
            self._advance(1)
        self._error("E-EXPR-002", "unterminated string", line, col)
        self._emit(TokenKind.ERROR, start, line, col)

    def _unexpected(self, ch: str) -> None:
        start, line, col = self.pos, self.line, self.col
        self._advance(1)
        message = f"unexpected character {ch!r}"
        if ch == "=":
            message += ": did you mean '=='?"
        self._error("E-EXPR-005", message, line, col)
        self._emit(TokenKind.ERROR, start, line, col)


def lex(text: str, line: int = 1, column: int = 1) -> tuple[list[Token], list[Diagnostic]]:
    """Split ``text`` into tokens. The last token is always EOF."""
    return _Lexer(text, line, column).run()
