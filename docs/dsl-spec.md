# DSL specification (draft 1)

Status: draft, written at the start of M1 (I1). It is refined while the lexer, the parser and the
shape validator are written, and every change is recorded in `journal.md`. The main example is
`examples/quarterly_consolidation.yaml`.

A workflow is a YAML document. Reading the YAML text into a tree with line and column positions is
done by `ruamel.yaml` (B1). Everything after that is ours: the shape of the document is checked by
our validator, and the strings inside the document that hold expressions are read by our lexer and
parser (C1, level 1).

## 1. Document shape

```text
workflow     : name (string), version (int >= 1)
inputs       : map  name -> input
steps        : map  name -> step            (at least one)
goals        : { any_of | all_of : list of step names }   (at least one step)

input        : { type: type, pattern?: string, default?: literal, required?: bool }

step         : {
  action      : string                         "package.name", must exist in the action registry
  with        : map  name -> value|expression  arguments of the action
  needs?      : list of step references        (default: none)
  join?       : { all_success | any_success | all_done } | { at_least: int >= 1 }
                                                (default: all_success)
  when?       : expression                     boolean; the step runs only if true
  ensures?    : expression                     boolean; checked after the action returns
  foreach?    : "name in catalogue.<list>"     expanded at publication
  on_failure? : fail | continue                (default: fail)
  retry?      : { max: int >= 0, backoff: fixed | exponential }
  compensate? : action name                    undo action used by the saga
}
```

Unknown keys are errors. The validator suggests the nearest known key when the edit distance is at
most 2 (`wen` gives `did you mean 'when'?`).

## 2. Names and references

```text
ident     : [A-Za-z_][A-Za-z0-9_]*
step_ref  : ident
            | ident '[' ident ']'        one instance of a foreach step, by the loop variable
            | ident '[' '*' ']'          every instance of a foreach step (fan-in)
path      : ident ( '.' ident | '[' (ident | int | '*') ']' )*
```

`needs` takes step references. A `foreach` step named `load` becomes the steps
`load[BIS]`, `load[OSC]`, ... when the version is published (DR-03: the graph is static).
`check[*]` in `needs` stands for all instances of `check`.

## 3. Expressions

An expression is the content of `when` and `ensures`; references are written `${path}` inside it
(`${approve.decision} == 'approved'`). A value inside `with` is a template: plain text with
`${path}` references (`${region.code}/${inputs.quarter}.xlsx`); a value that is exactly one reference
keeps the type of the referenced output. The template splitter arrives with the parser.

```text
expr      : or_expr
or_expr   : and_expr ( 'or' and_expr )*
and_expr  : not_expr ( 'and' not_expr )*
not_expr  : 'not' not_expr | cmp_expr
cmp_expr  : add_expr ( ('==' | '!=' | '<' | '<=' | '>' | '>=' | 'in') add_expr )?
add_expr  : mul_expr ( ('+' | '-') mul_expr )*
mul_expr  : unary ( ('*' | '/' | '%') unary )*
unary     : '-' unary | postfix
postfix   : primary ( '.' ident | '[' expr ']' | '(' [ expr ( ',' expr )* ] ')' )*
primary   : literal | '${' path '}' | ident | '(' expr ')'
literal   : int | float | string ('...' or "...") | 'true' | 'false' | 'null'
```

Precedence from the loosest to the tightest: `or`, `and`, `not`, comparison, `+ -`, `* / %`,
unary minus, postfix. Comparison does not chain: `a < b < c` is an error (E-EXPR-003). Built-in functions:
`len`, `sum`, `min`, `max`, `count`, `exists`.

An expression outside the analysable fragment (a function call that is not built in, a regular
expression, arithmetic on two variables with a multiplication) is still valid. The static analysis
treats it as "unknown", never as "proved" (invariants in `architecture.md`).

## 4. Types

```text
type  : string | int | float | bool | date | list<type> | record{ name: type, ... } | error
```

- Structural subtyping: a record with more fields is a subtype of a record with fewer fields;
  `int` is a subtype of `float`.
- Fan-in: the type of `check[*].rows` is `list<T>` where `T` is the type of one `check[r].rows`.
- A step that is allowed to fail (`on_failure: continue`) has the output type `T | error` for its
  consumers; reading it without a guard is a type error (phase 4, M5).

## 5. Join rules

| Rule | The step is ready when | The step fails when |
| --- | --- | --- |
| `all_success` | every dependency succeeded | one dependency failed or was skipped |
| `any_success` | at least one dependency succeeded and none is still running | all dependencies failed or were skipped |
| `all_done` | every dependency finished, in any state | never because of dependencies |
| `at_least: k` | at least `k` dependencies succeeded | fewer than `k` can still succeed |

A skipped step is one whose `when` was false or whose join can no longer be satisfied. The goals
decide the result of the run: SUCCEEDED when the `goals` condition holds, GOAL_NOT_REACHED when the
run ended and it does not (DR-02).

## 6. Diagnostics

Every diagnostic has a code, a severity, a message, a line and a column in the original text (also
for steps that came from `foreach` expansion, through the source map).

| Code | Severity | Meaning | Produced by |
| --- | --- | --- | --- |
| E-SHAPE-001 | error | unknown key (with did-you-mean) | shape validator |
| E-SHAPE-002 | error | required key missing | shape validator |
| E-SHAPE-003 | error | value has the wrong kind (map, list, scalar) | shape validator |
| E-EXPR-001 | error | unexpected token in an expression | parser |
| E-EXPR-002 | error | unterminated string or `${` | lexer |
| E-EXPR-003 | error | chained comparison | parser |
| E-EXPR-004 | error | invalid escape sequence in a string | lexer |
| E-EXPR-005 | error | unexpected character (a lone `=` suggests `==`) | lexer |
| E-EXPR-006 | error | expression nested more than 128 levels | parser |
| E-REF-001 | error | reference to an unknown step, input or catalogue list | phase 1-2 |
| E-GRAPH-001 | error | dependency cycle (the shortest cycle is reported) | phase 3 |
| E-TYPE-001 | error | type mismatch | phase 4 (M5) |
| W-TYPE-002 | warning | output that may be an error is read without a guard | phase 4 (M5) |

The parser does not stop at the first error: it inserts an error node and continues at the next
synchronisation token (`)`, `]`, `,`, `and`, `or`, end of the string), so one run reports all
syntax errors of a document.

## 7. Open points for M1

- ~~Escape sequences in strings~~ Closed 3 November: `\n \t \r \\ \' \"` are the escapes; any other escape is E-EXPR-004 and the character is kept.
- Whether `in` takes a list literal (`x in [1, 2]`) in M1 or only a catalogue list.
- Exact `retry` semantics when `ensures` fails (retry the action, or fail at once).
