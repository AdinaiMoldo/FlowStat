"""Expression parser (C1, level 1): recursive descent with precedence climbing.

Binary operators are handled by one loop driven by a precedence table instead of one function per
level. The parser never raises. On a syntax error it records a diagnostic, puts an ErrorNode into
the tree and skips to a synchronisation token (a closing bracket, a comma, ``and``, ``or`` or the
end), so that one run reports every syntax error of an expression.
"""

from __future__ import annotations

from flowstat.dsl.ast import (
    Attr,
    Binary,
    Call,
    Compare,
    ErrorNode,
    Expr,
    Index,
    Literal,
    Name,
    PathPart,
    Ref,
    Unary,
)
from flowstat.dsl.lexer import Token, TokenKind, lex
from flowstat.model.diagnostics import Diagnostic, Severity

K = TokenKind

MAX_DEPTH = 128

# precedence: higher binds tighter. "not" sits at level 3, between "and" and comparison.
_BINARY: dict[TokenKind, tuple[int, str]] = {
    K.OR: (1, "or"),
    K.AND: (2, "and"),
    K.EQ: (4, "=="),
    K.NE: (4, "!="),
    K.LT: (4, "<"),
    K.LE: (4, "<="),
    K.GT: (4, ">"),
    K.GE: (4, ">="),
    K.IN: (4, "in"),
    K.PLUS: (5, "+"),
    K.MINUS: (5, "-"),
    K.STAR: (6, "*"),
    K.SLASH: (6, "/"),
    K.PERCENT: (6, "%"),
}
_COMPARISON = 4
_NOT_LEVEL = 3

_SYNC = {K.RPAREN, K.RBRACKET, K.COMMA, K.AND, K.OR, K.RBRACE, K.EOF}
_WORDS = {K.IDENT, K.AND, K.OR, K.NOT, K.IN, K.TRUE, K.FALSE, K.NULL}
_LITERALS = {K.INT, K.FLOAT, K.STRING, K.TRUE, K.FALSE, K.NULL}


def _describe(tok: Token) -> str:
    return "end of input" if tok.kind is K.EOF else f"'{tok.text}'"


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.depth = 0
        self.aborted = False
        self.diags: list[Diagnostic] = []

    # -- helpers -----------------------------------------------------------------------
    @property
    def tok(self) -> Token:
        return self.tokens[self.pos]

    def advance(self) -> Token:
        tok = self.tokens[self.pos]
        if tok.kind is not K.EOF:
            self.pos += 1
        return tok

    def error(self, code: str, message: str, tok: Token) -> None:
        if not self.aborted:
            self.diags.append(Diagnostic(code, Severity.ERROR, message, tok.line, tok.column))

    def error_node(self, message: str, tok: Token) -> ErrorNode:
        return ErrorNode(tok.line, tok.column, message)

    def abort(self, tok: Token) -> ErrorNode:
        self.error("E-EXPR-006", "expression is nested too deeply", tok)
        self.aborted = True
        self.pos = len(self.tokens) - 1
        return self.error_node("nested too deeply", tok)

    def skip_to_sync(self) -> None:
        while self.tok.kind not in _SYNC:
            self.advance()

    def expect(self, kind: TokenKind, what: str) -> bool:
        if self.tok.kind is kind:
            self.advance()
            return True
        self.error("E-EXPR-001", f"expected {what}, found {_describe(self.tok)}", self.tok)
        return False

    # -- expressions ---------------------------------------------------------------------
    def parse_binary(self, min_prec: int) -> Expr:
        self.depth += 1
        try:
            if self.depth > MAX_DEPTH:
                return self.abort(self.tok)
            return self._binary(min_prec)
        finally:
            self.depth -= 1

    def _binary(self, min_prec: int) -> Expr:
        left: Expr
        if self.tok.kind is K.NOT and min_prec <= _NOT_LEVEL:
            tok = self.advance()
            left = Unary(tok.line, tok.column, "not", self.parse_binary(_NOT_LEVEL))
        else:
            left = self.parse_unary()
        while True:
            entry = _BINARY.get(self.tok.kind)
            if entry is None or entry[0] < min_prec:
                return left
            prec, op = entry
            self.advance()
            if prec == _COMPARISON:
                left = Compare(left.line, left.column, op, left, self.parse_binary(prec + 1))
                nxt = _BINARY.get(self.tok.kind)
                while nxt is not None and nxt[0] == _COMPARISON:
                    self.error("E-EXPR-003", "comparisons cannot be chained; use 'and'", self.tok)
                    self.advance()
                    self.parse_binary(prec + 1)
                    nxt = _BINARY.get(self.tok.kind)
            else:
                left = Binary(left.line, left.column, op, left, self.parse_binary(prec + 1))

    def parse_unary(self) -> Expr:
        if self.tok.kind is K.MINUS:
            tok = self.advance()
            self.depth += 1
            try:
                if self.depth > MAX_DEPTH:
                    return self.abort(tok)
                return Unary(tok.line, tok.column, "-", self.parse_unary())
            finally:
                self.depth -= 1
        return self.parse_postfix()

    def parse_postfix(self) -> Expr:
        expr = self.parse_primary()
        while True:
            kind = self.tok.kind
            if kind is K.DOT:
                self.advance()
                if self.tok.kind in _WORDS:
                    name = self.advance().text
                    expr = Attr(expr.line, expr.column, expr, name)
                else:
                    self.error(
                        "E-EXPR-001",
                        f"expected a name after '.', found {_describe(self.tok)}",
                        self.tok,
                    )
                    return expr
            elif kind is K.LBRACKET:
                self.advance()
                index = self.parse_binary(1)
                self.expect(K.RBRACKET, "']'")
                expr = Index(expr.line, expr.column, expr, index)
            elif kind is K.LPAREN:
                self.advance()
                args: list[Expr] = []
                if self.tok.kind is not K.RPAREN:
                    args.append(self.parse_binary(1))
                    while self.tok.kind is K.COMMA:
                        self.advance()
                        args.append(self.parse_binary(1))
                self.expect(K.RPAREN, "')'")
                expr = Call(expr.line, expr.column, expr, tuple(args))
            else:
                return expr

    def parse_primary(self) -> Expr:
        tok = self.tok
        kind = tok.kind
        if kind in _LITERALS:
            self.advance()
            return Literal(tok.line, tok.column, tok.value)
        if kind is K.IDENT:
            self.advance()
            return Name(tok.line, tok.column, str(tok.value))
        if kind is K.REF_OPEN:
            return self.parse_ref()
        if kind is K.LPAREN:
            self.advance()
            inner = self.parse_binary(1)
            self.expect(K.RPAREN, "')'")
            return inner
        if kind is K.ERROR:
            self.advance()  # the lexer has already reported it
            return self.error_node("invalid token", tok)
        self.error("E-EXPR-001", f"expected an expression, found {_describe(tok)}", tok)
        self.skip_to_sync()
        return self.error_node("expected an expression", tok)

    def parse_ref(self) -> Expr:
        open_tok = self.advance()  # ${
        parts: list[PathPart] = []
        ok = True
        if self.tok.kind in _WORDS:
            parts.append(PathPart("field", self.advance().text))
        else:
            ok = self._ref_error("a name")
        while ok:
            kind = self.tok.kind
            if kind is K.DOT:
                self.advance()
                if self.tok.kind in _WORDS:
                    parts.append(PathPart("field", self.advance().text))
                else:
                    ok = self._ref_error("a name after '.'")
            elif kind is K.LBRACKET:
                self.advance()
                ok = self._ref_subscript(parts)
            else:
                break
        if ok and self.tok.kind is not K.RBRACE:
            if self.tok.kind is not K.EOF:  # at the end the lexer reports the unclosed '${'
                self.error("E-EXPR-001", f"expected '}}', found {_describe(self.tok)}", self.tok)
            ok = self.tok.kind is K.EOF
        if self.tok.kind is not K.RBRACE and not ok:
            while self.tok.kind not in (K.RBRACE, K.EOF):
                self.advance()
        if self.tok.kind is K.RBRACE:
            self.advance()
        elif ok:
            return Ref(open_tok.line, open_tok.column, tuple(parts))
        if not ok:
            return self.error_node("invalid reference", open_tok)
        return Ref(open_tok.line, open_tok.column, tuple(parts))

    def _ref_error(self, what: str) -> bool:
        self.error(
            "E-EXPR-001", f"expected {what} in a reference, found {_describe(self.tok)}", self.tok
        )
        return False

    def _ref_subscript(self, parts: list[PathPart]) -> bool:
        tok = self.tok
        if tok.kind is K.IDENT:
            parts.append(PathPart("var", str(tok.value)))
        elif tok.kind is K.INT:
            parts.append(PathPart("index", int(str(tok.value))))
        elif tok.kind is K.STAR:
            parts.append(PathPart("star", "*"))
        else:
            return self._ref_error("a variable, a number or '*'")
        self.advance()
        if self.tok.kind is K.RBRACKET:
            self.advance()
            return True
        return self._ref_error("']'")


def parse_expression(text: str, line: int = 1, column: int = 1) -> tuple[Expr, list[Diagnostic]]:
    """Parse an expression. Returns a tree (with ErrorNodes where needed) and all diagnostics."""
    tokens, lex_diags = lex(text, line, column)
    parser = _Parser(tokens)
    if parser.tok.kind is K.EOF:
        parser.error("E-EXPR-001", "the expression is empty", parser.tok)
        tree: Expr = parser.error_node("empty expression", parser.tok)
    else:
        tree = parser.parse_binary(1)
        trailing = parser.tok
        if trailing.kind not in (K.EOF, K.ERROR) and not parser.diags:
            parser.error(
                "E-EXPR-001",
                f"unexpected {_describe(trailing)} after the end of the expression",
                trailing,
            )
    diags = sorted([*lex_diags, *parser.diags], key=lambda d: (d.line, d.column))
    return tree, diags
