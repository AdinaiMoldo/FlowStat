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

input        : { type: type, pattern?: string, ref?: catalogue list, default?: literal,
                 required?: bool }                (required by default unless there is a default)

step         : {
  action      : string                         "package.name", must exist in the action registry
  with        : map  name -> value|expression  arguments of the action
  needs?      : list of step references        (default: none)
  join?       : { all_success | any_success | all_done } | { at_least: int >= 1 }
                                                (default: all_success)
  when?       : expression                     boolean; the step runs only if true
  ensures?    : expression                     boolean; checked after the action returns
  foreach?    : "name in catalogue.<list>"     expanded at publication
  on_failure? : fail | continue | compensate   (default: fail)
  retry?      : { max: int >= 0, backoff: fixed | exponential, delay?: number > 0 }
                                                max = retries after the first attempt;
                                                delay in seconds (default 1)
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

`needs` takes step references. In YAML a reference with brackets must be quoted: `needs: ["load[region]"]` (an unquoted `[load[region]]` is a YAML syntax error). A `foreach` step named `load` becomes the steps
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

### Inputs and the catalogue

`ref: regions` limits an input of type `string` (or `list<string>`) to the codes of the catalogue
list `regions`. The values are checked when a run starts, against the catalogue snapshot stored with
the published version, so a later change of the live catalogue does not change a published version.
An invalid regular expression in `pattern` is E-SHAPE-004. Only the whole text must match a pattern.

### 3.1 Evaluation (runtime meaning)

Values are `null`, `bool`, `int`, `float`, `string`, `list` and `record`. The engine evaluates
an expression with the inputs of the run and the outputs of the steps that SUCCEEDED.

- `${inputs.x}` reads an input. `${step.field}` reads a field of the output of a step; for an
  instance of a foreach step the name is the instance (`load[BIS]`). `${check[*].rows}` is the list
  of `rows` of the instances of `check` that SUCCEEDED, in declaration order; failed, skipped and
  cancelled instances are left out.
- Arithmetic follows Python for numbers: `+ - * %` keep integers, `/` gives a float, and `int`
  and `float` compare by value. `+` also joins two strings. Other mixes are a type error.
- `==` and `!=` accept any two values; `true` is not equal to `1`. `< <= > >=` accept two numbers or
  two strings. `x in y` accepts a list (membership), a string (substring, `x` a string) or a
  record (key).
- `and`, `or` and `not` need boolean operands and stop as soon as the answer is known. `when` and
  `ensures` must give a boolean.
- Built-in functions: `len(list | string | record)`, `sum(list of numbers)`, `min` and `max` (a
  non-empty list of numbers, or of strings), `count(list)` (the items that are not null) and
  `exists(reference)`. `exists` is true when the reference can be read and is not null, and false
  when the step has no output, the field or the index is not there, or the value is null; other
  errors in its argument are still errors. It is the guard for outputs of steps that may have failed.
- A template value (`with`) that is exactly one reference keeps the type of the value; otherwise
  the parts are joined as text (a boolean is `true` or `false`; `null`, lists and records are an
  error).

Runtime errors do not stop the engine: the step that evaluates the expression FAILS with the code
and the position of the expression.

| Code | Meaning |
| --- | --- |
| E-RUN-001 | unknown name or function |
| E-RUN-002 | the step has no output (it did not succeed), or the input does not exist |
| E-RUN-003 | the field does not exist, or the value is `null` |
| E-RUN-004 | division or remainder by zero |
| E-RUN-005 | type error (operands do not fit the operator) |
| E-RUN-006 | index out of range or of the wrong kind |
| E-RUN-007 | wrong number of arguments of a function, or an invalid argument |
| E-RUN-008 | `when` or `ensures` did not give a boolean |

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

### 5.1 How the engine decides a step

A step is decided only when all its dependencies are terminal (SUCCEEDED, FAILED, SKIPPED,
UPSTREAM_FAILED or CANCELLED), so the result of a run never depends on timing. There is no early
start once a quorum is reached.

1. The join rule is applied. If it is not satisfied the step takes a terminal state without running:
   UPSTREAM_FAILED when failures caused the shortfall, SKIPPED otherwise. For `all_success` and
   `any_success` this means: UPSTREAM_FAILED if some dependency FAILED or was UPSTREAM_FAILED,
   else SKIPPED. For `at_least: k` with s successes and f failures: UPSTREAM_FAILED when
   f > 0 and s + f >= k (failures could have filled the quorum), else SKIPPED.
2. `when` is evaluated. False gives SKIPPED; an evaluation error gives FAILED with the code of the
   error.
3. The step is READY. Ready steps start in the order of the longest chain of steps behind them
   (the plain critical path, weights are the average durations, default 1), then in declaration
   order, while fewer than `m` steps are RUNNING. A WAITING step does not use a slot.
4. When the action returns, `ensures` is evaluated with the output of the step visible under its
   own name. False gives FAILED with E-CONTRACT-001, an evaluation error gives FAILED with its code.
   A step that broke its contract has no output.

A step that FAILS with `on_failure: fail` (the default) stops the run: running steps are cancelled,
every step that is not finished becomes CANCELLED and the run ends as FAILED. With
`on_failure: continue` the failure only travels through the join rules of the steps downstream.
`retry` is applied from M3 (section 5.3); `on_failure: compensate` runs the saga of section 5.4.

### 5.2 Actions

An action is a registered function with a declared interface: the names and types of its
arguments and of the fields of its output. The registry checks both on every call, so a step whose
action returns a different shape fails with E-ACT-004 instead of passing a wrong value on. The
workflow text does not repeat these schemas.

| Action | Arguments | Output | Notes |
| --- | --- | --- | --- |
| `xlsx.read` | `path: string` | `rows: list`, `count: int` | first sheet; header `code, name, value`; empty rows ignored; path is relative to the data directory |
| `statguard.validate` | `rows: list` | `rows: list`, `checked: int` | codes non-empty and unique, values numbers and not negative, at least one row |
| `stats.aggregate` | `parts: list` | `rows: list`, `regions: int` | sum of `value` by `code`, in code order |
| `approval.wait` | `role: string` | the answer (`decision`, `by`) | pauses the step; the driver supplies the answer |
| `db.write` | `rows: list` | `written: int`, `batch: string` | batch is the run id; writing again replaces (idempotent) |
| `db.delete_batch` | none | `deleted: int` | removes the batch of the run (compensation, used from M3) |
| `email.send` | none | `queued: int` | puts a message in the outbox table; nothing is sent |

### 5.3 Retry and idempotency keys

`retry: {max: n, backoff: fixed | exponential, delay: d}` retries a step after a *transient*
failure. `max` counts retries, so the step makes at most `n + 1` attempts; `delay` is in seconds and
defaults to 1.

- Transient or not is decided by the action, not by the workflow: a lost connection, a timeout and an HTTP 5xx are transient; a missing file, a failed validation, an HTTP 4xx and an unexpected exception are not. A permanent failure fails the step at once, whatever `retry` says.
- A failed contract (`ensures`) or an error in an expression is never retried: the action returned, so the failure is in the result and a second call would repeat its side effect for nothing.
- The delay before the retry number k (k = 1, 2, ...) is `d` for `fixed` and `d * 2^(k-1)` for `exponential`. There is no jitter, so a run is reproducible.
- During the delay the step stays RUNNING and keeps its slot; each retry is an event (`STEP_RETRY`) with the attempt number, the delay and the error that caused it.
- When the retries are used up the step FAILS with the code and message of the last error, and its `on_failure` policy applies.
- Every attempt carries the idempotency key `run id / step id / attempt number` (attempts start at 1). An action that honours the key makes a repeated attempt harmless. After a crash the lost attempt is run again with the same key (section 5.5).

### 5.4 Compensation (saga)

`on_failure: compensate` stops the run as `fail` does (running steps are cancelled, steps not yet
finished become CANCELLED) and then undoes the completed work.

1. The steps to undo are those that SUCCEEDED and declare `compensate: action`.
2. A step is undone only after every step it can reach (its descendants through `needs`, including steps without `compensate` in between) that is also to be undone has been undone. Independent compensations run in parallel, within the limit of `m`.
3. The compensation action receives the arguments the step had when it ran (`with`, rendered again from the recorded outputs) and the key `run id / step id / compensate / attempt`.
4. A compensation that fails transiently is retried with the `retry` of its step, or with 3 retries and exponential backoff when the step has none. When the retries are used up the compensation fails; the steps that were waiting for it stay uncompensated, the others continue.
5. The run ends COMPENSATED when every step in the list was undone (also when the list is empty), and COMPENSATION_FAILED otherwise, with the list of steps that remain.

Which steps had succeeded at the moment of the failure depends on how many steps ran at once, as with every stop (running steps are cancelled whatever their progress); the compensation is exact for the steps that had succeeded. A cancelled step that was already running may have had an effect that is not recorded; this is a stated limit. Once the compensation has begun it is not interrupted: a request to cancel the run is ignored until it ends.

A step that was undone has the state COMPENSATED instead of SUCCEEDED; a step whose compensation is
running is COMPENSATING and one whose compensation failed is COMPENSATION_FAILED. An effect that
cannot be undone (an e-mail that was sent) is expressed by not declaring `compensate`; the step is
then simply not in the list. A warning for a step with side effects and no `compensate` needs
information about the actions and comes with the static analysis (M5).

### 5.5 Crash recovery and uncertain steps

The history of a run is a write-ahead log: the events a scheduler step produced are stored and committed *before* the commands they
produced are executed and before a signal is acknowledged.

After a crash the engine reads the events of every run that did not finish and rebuilds the state
from them (no decision is taken again from scratch; the recorded states are the state). Then:

- a step that was WAITING stays WAITING; its answer is still awaited;
- a step with all dependencies finished and no events of its own is decided again (the decision is a function of recorded values, so it comes out the same);
- a step that was RUNNING and whose action is **idempotent** is run again with the *same* attempt number and so the same key. If the first call had taken effect, the receiver recognises the key and does nothing twice;
- a step that was RUNNING and whose action is **not idempotent** becomes UNCERTAIN (code E-REC-001). Nothing is run again automatically: the effect may or may not exist, and only a person (or the receiving system) can tell. Steps that depend on it wait, independent steps go on, and the run is WAITING when nothing else can move.

An operator resolves an UNCERTAIN step in one of two ways: **done** (the effect exists; the operator may
give the output that the step would have produced, `{}` by default), which makes the step SUCCEEDED, or
**retry** (the effect does not exist, or a repeat is acceptable), which starts a new attempt with the
next attempt number. Both are recorded as events with the time and the decision.

A run that was COMPENSATING continues its compensation: compensations that have a COMPENSATED event are
not repeated; the one that was in flight follows the rule above, so its action must be idempotent
(a non-idempotent compensation becomes UNCERTAIN in the same way).

Whether an action is idempotent is part of its declared interface (the registry, section 5.2):
`xlsx.read`, `statguard.validate`, `stats.aggregate`, `db.write`, `db.delete_batch` and `http.call`
(which sends the key) are idempotent; `email.send` is not.

Limits, stated: exactly-once delivery is impossible without the cooperation of the receiving system,
so for an action that ignores the key the guarantee is "at most once automatically, otherwise
UNCERTAIN"; a clock jump is not part of the fault model; one process owns a run at a time (the owner
switch is C5, M8).

## 6. Diagnostics

Every diagnostic has a code, a severity, a message, a line and a column in the original text (also
for steps that came from `foreach` expansion, through the source map).

| Code | Severity | Meaning | Produced by |
| --- | --- | --- | --- |
| E-SHAPE-001 | error | unknown key (with did-you-mean) | shape validator |
| E-SHAPE-002 | error | required key missing | shape validator |
| E-SHAPE-003 | error | value has the wrong kind (map, list, scalar) | shape validator |
| E-SHAPE-004 | error | value not allowed (not in the list, fails the pattern, below the minimum, empty) | shape validator |
| E-SHAPE-005 | error | not exactly one of several alternative keys (for example `goals`) | shape validator |
| E-YAML-001 | error | YAML syntax error | reader |
| E-YAML-002 | error | duplicate key | reader |
| E-YAML-003 | error | empty document | reader |
| E-YAML-004 | error | anchors and aliases are not supported | reader |
| E-EXPR-001 | error | unexpected token in an expression | parser |
| E-EXPR-002 | error | unterminated string or `${` | lexer |
| E-EXPR-003 | error | chained comparison | parser |
| E-EXPR-004 | error | invalid escape sequence in a string | lexer |
| E-EXPR-005 | error | unexpected character (a lone `=` suggests `==`) | lexer |
| E-EXPR-006 | error | expression nested more than 128 levels | parser |
| E-REF-001 | error | unknown step, input or goal | compiler |
| E-REF-002 | error | malformed step reference in `needs` | compiler |
| E-REF-003 | error | catalogue list used by `foreach` is empty | compiler |
| E-REF-004 | error | unknown attribute of a loop variable | compiler |
| E-REF-005 | error | loop variable not defined for this step, or no such instance | compiler |
| E-REF-006 | error | `[*]` on a step that is not a foreach step | compiler |
| E-REF-007 | error | step is used but is not a dependency (not an ancestor) | compiler |
| E-REF-008 | error | unknown action | compiler |
| E-REF-009 | error | a goal names a foreach step | compiler |
| E-REF-010 | error | `at_least` larger than the number of dependencies | compiler |
| E-REF-011 | error | indexing a step by number is not supported | compiler |
| E-REF-012 | error | unknown catalogue list | compiler |
| E-REF-013 | error | foreach step referenced without `[var]` or `[*]` | compiler |
| E-REF-014 | error | `ref` on an input that is not a string or a list of strings | compiler |
| E-REF-015 | error | unknown compensation action | compiler |
| E-GRAPH-001 | error | dependency cycle (the shortest one is reported) | compiler |
| E-CAT-001 to E-CAT-004 | error | invalid catalogue file (shape, item, missing or duplicate code) | catalogue loader |
| E-INPUT-001 to E-INPUT-005 | error | value of a run input: missing, wrong type, pattern, not in the catalogue list, unknown input | input validation |
| E-VER-001 | error | the `version` in the text differs from the version number being published | service |
| E-VER-002 | error | the `workflow` name in the text differs from the name it is saved under | service |
| E-RUN-001 to E-RUN-008 | error | runtime error of an expression, see 3.1 (the step FAILS) | engine |
| E-CONTRACT-001 | error | `ensures` is false after the action returned (the step FAILS) | engine |
| E-ACT-001 | error | the action is not registered (the step FAILS) | actions |
| E-ACT-002 | error | a file cannot be read; the message has the path as written in `with` | actions |
| E-ACT-003 | error | the file is not a valid form (not an xlsx file, or the first row is not `code, name, value`) | actions |
| E-ACT-004 | error | the output of an action does not have its declared schema | actions |
| E-ACT-005 | error | `statguard.validate`: rows break a rule; the message names the first rows | actions |
| E-ACT-006 | error | the target database cannot be opened, or the rows cannot be written | actions |
| E-ACT-007 | error | a path leads outside the data directory | actions |
| E-ACT-009 | error | an argument of the action is missing or has the wrong type | actions |
| E-REC-001 | error | the outcome of a non-idempotent step is unknown after a crash (the step is UNCERTAIN) | recovery |
| E-COMP-001 | error | a compensation failed after its retries (the step is COMPENSATION_FAILED) | recovery |
| E-ACT-008 | error | an HTTP call failed in transport, timed out or got a 5xx answer (transient) | actions |
| E-ACT-010 | error | an HTTP call got a 4xx answer (permanent) | actions |
| E-ACT-999 | error | an action raised an exception it did not expect (the step FAILS, the run goes on) | engine |
| E-TYPE-001 | error | type mismatch | phase 4 (M5) |
| W-TYPE-002 | warning | output that may be an error is read without a guard | phase 4 (M5) |

The parser does not stop at the first error: it inserts an error node and continues at the next
synchronisation token (`)`, `]`, `,`, `and`, `or`, end of the string), so one run reports all
syntax errors of a document.

## 7. Open points for M1

- ~~Escape sequences in strings~~ Closed 3 November: `\n \t \r \\ \' \"` are the escapes; any other escape is E-EXPR-004 and the character is kept.
- Whether `in` takes a list literal (`x in [1, 2]`) in M1 or only a catalogue list.
- ~~Exact `retry` semantics when `ensures` fails~~ Closed 5 November: the step fails at once and is not retried (section 5.3).
