"""B1 YAML reading and C1 lexer, parser and shape validator. Layer 1.

Turns DSL text into a tree with positions.

reader.py     B1: ruamel.yaml reads the YAML text into a tree with line and column positions.
              The only third-party import allowed in the compiler packages.
shape.py      C1: document shape validator written by us (table of node kinds, did-you-mean).
lexer.py      C1: expression tokens with positions.
parser.py     C1: recursive descent with precedence climbing, error nodes and recovery.
ast.py        C1: expression AST shared with the evaluator of the engine.

Built in I1. Algorithm card: docs/algorithms/C1.md.
"""
