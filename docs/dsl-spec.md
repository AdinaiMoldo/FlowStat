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
                 required?: bool,                 (required by default unless there is a default)
                 values?: list of literals,       the allowed constants (string, int, float, bool)
                 min?: number, max?: number }     inclusive bounds (int, float); see 8.2

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
  timeout?    : number > 0                     seconds a step that waits for a person may wait
                                                (only for an action that waits, section 5.6)
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
primary   : literal | list | '${' path '}' | ident | '(' expr ')'
list      : '[' [ expr ( ',' expr )* ] ']'
literal   : int | float | string ('...' or "...") | 'true' | 'false' | 'null'
```

Precedence from the loosest to the tightest: `or`, `and`, `not`, comparison, `+ -`, `* / %`,
unary minus, postfix. Comparison does not chain: `a < b < c` is an error (E-EXPR-003). Built-in functions:
`len`, `sum`, `min`, `max`, `count`, `exists`.

A list literal is a primary, so `${approve.decision} in ['approved', 'escalated']` is an expression;
at run time it is a list value and `in` tests membership (3.1). `[` after a primary is an index and
at the start of a primary it is a list, so the two do not clash.

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

### 3.2 Static analysis of guards (the fragment of C6)

The analysis of M4 reads the `when` of a step and answers three questions: can two guards hold at
once, does a set of guards cover every case, can a guard hold at all. It does so with the solver
(`solver/`), which handles a small fragment exactly and says "unknown" for everything else.

**Variables and domains.** A variable is a reference whose domain is known:

- an input: its declared type (`int`, `float`, `bool`, `string`), narrowed by the optional keys
  `values` (the allowed constants), `min` and `max` (section 8.2);
- a field of the output of a step, with the type the action declares for it;
- the field `decision` of `approval.wait`: an enumeration whose values are the argument
  `decisions` of the step, `[approved, rejected]` when it is not given.

A reference whose domain is not known (a list, a record, a field of an action without a declared
schema, a reference through `[*]`) is opaque.

**The fragment.** An atom is one of

- `x op c` or `c op x`, `op` one of `== != < <= > >=`, `x` a variable and `c` a literal;
- `x + c op d`, `x - c op d` (the constant moves to the right);
- `x - y op c` and `x op y + c`, with `x` and `y` numeric variables;
- `x in [c1, c2, ...]` with literals;
- a boolean variable on its own.

Atoms are combined with `and`, `or` and `not`. Negation is pushed to the atoms (`not (x < 3)` is
`x >= 3`), so the fragment is closed under negation. A comparison of two literals is evaluated.
Everything else is an opaque atom: a call (`len`, `exists`, `sum`, ...), `*`, `/`, `%`, an ordering
of strings, a comparison with `null`, a list or a record. A comparison of two different types is
`false` for `==` and `true` for `!=` (3.1); an ordering of two different types is opaque, because
the run would fail and not branch.

**Numbers.** Constants and weights are exact rationals, read from the text of the literal. Over
integers a strict atom is tightened (`x < 6` is `x <= 5`); a relation between an integer and a
float variable is read over the real numbers. A float input is a real number: `x > 3` has a
witness (`3.5`), and a witness is always a number that the engine's evaluator confirms.

**Answers.** A question is answered

- `sat` with a witness (an assignment of the variables that the evaluator of 3.1 accepts),
- `unsat`, proved, or
- `unknown`.

A clause with an opaque atom is `unsat` only when the rest of the clause is `unsat` without it;
otherwise it is `unknown`, never `sat`. A predicate whose disjunctive normal form has more than 64
clauses is `unknown`. No warning is based on `unknown` (section 8).

**How it is decided.** The predicate becomes a disjunction of clauses (DNF with the cap). A clause
is a conjunction of atoms. Each variable gets its domain from the atoms on it alone (a union of
intervals for numbers, a finite set for enumerations and booleans, a finite or cofinite set for
strings). The relations between two variables are the edges of a difference graph with weights
`(c, k)`, `k` the number of strict edges; the clause is unsatisfiable when the graph has a negative
cycle (`c < 0`, or `c = 0` and `k > 0`), found by Bellman-Ford. A witness is built from the
shortest distances, then moved to a boundary value inside the domain of each variable. The check
of the second implementation (Floyd-Warshall) is a test, not part of the product.

**Questions the analysis asks.** `sat(p)`; `exclusive(p, q)` is `unsat(p and q)`; `entails(p, q)` is
`unsat(p and not q)`; `exhaustive(p1..pn)` is `unsat(not p1 and ... and not pn)`, and its witness
is the uncovered case.

## 4. Types

```text
type  : string | int | float | bool | date | null | list<type> | record{ name: type, ... } | any
```

- Structural subtyping: a record with more fields is a subtype of a record with fewer fields;
  `int` is a subtype of `float`; `list<S>` is a subtype of `list<T>` when `S` is a subtype of `T`.
- `any` is the type of what the checker cannot know (an output field that the action does not
  declare, an empty list literal). It fits every type and every operator and never causes an
  error, so one mistake gives one message and no follow-up.
- Fan-in: the type of `check[*].rows` is `list<T>` where `T` is the type of one `check[r].rows`.
  The list holds the instances that SUCCEEDED (3.1), so its length is not fixed (section 10).
- `date` is not a literal of the language: it is passed from an input to an argument, and two
  dates compare with each other.
- `null` is the type of the literal; it is compared with `==` and `!=` only.
- A step that is allowed to fail (`on_failure: continue`) has no output when it failed. Reading
  it where the join lets the consumer start after the failure is a question of availability, not
  of types (section 10); there is no `T | error` type.

The names `string`, `int`, `float`, `bool` and `date` are those of the `type` of an input (an
input of type `list<string>` has that type). The shape of a row of a regional form is the named
type `row`, an abbreviation for `record{code: string, name: string, value: float}`.

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
   (weights are the average durations, default 1; the feasible chain of section 11 with the
   inputs of the run, the plain critical path when no priorities are given), then in declaration
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
| `xlsx.read` | `path: string` | `rows: list<row>`, `count: int` | first sheet; header `code, name, value`; empty rows ignored; path is relative to the data directory |
| `statguard.validate` | `rows: list<row>`; optional `rules: string` | `rows: list<row>`, `checked: int` | codes non-empty and unique, values numbers and not negative, at least one row; with `rules: regional-indicators-v1` also every indicator present, ranges, relations between indicators and exact sums (`model/rules.py`, api-spec 11); only errors fail the step |
| `stats.aggregate` | `parts: list<list<row>>` | `rows: list<row>`, `regions: int` | sum of `value` by `code`, in code order |
| `approval.wait` | `role: string`; optional `decisions: list<string>` | `decision: string`, `by: string` | pauses the step; the driver supplies the answer |
| `db.write` | `rows: list<row>` | `written: int`, `batch: string` | batch is the run id; writing again replaces (idempotent); has an effect (10.5) |
| `db.delete_batch` | none | `deleted: int` | removes the batch of the run (compensation, used from M3) |
| `email.send` | none | `queued: int` | puts a message in the outbox table; nothing is sent; has an effect (10.5) |
| `http.call` | `url: string`; optional `method: string`, `body: any`, `timeout: float` | `status: int` | 5.3; has an effect (10.5) |

The types are written in the notation of section 4 and are what the type check of section 9 reads;
the registry derives the Python type it checks at run time from the same declaration.

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
- A receiver outside the engine gets a different key, the key of the *operation*: `run id / step id` (and `run id / step id / compensate` for an undo), the same for every attempt. After a lost answer a retry has a new attempt number; if the receiver saw that number as a new key it would make the effect a second time. The attempt key is what the engine records, the operation key is what `http.call` sends in the header `Idempotency-Key`.

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

How it is done, and what follows from it:

- The events of every call into the scheduler are committed in one transaction before the commands
  of that call are dispatched (`recovery/wal.py`, `synchronous = FULL`). A crash therefore leaves a
  history that ends at a scheduler step; a history cut in the middle of one (a prefix of the events)
  is also restored correctly, and the crash tests cut after every single event.
- The restore adds the event RUN_RESUMED. A stop that had been recorded only in part (a failure
  stored, the cancellations not) is completed. A restored run that already has its outcome is left
  as it is.
- A step that becomes UNCERTAIN gets the event STEP_UNCERTAIN (code E-REC-001, the attempt whose
  result is unknown, and the state it was in: RUNNING, or COMPENSATING for an undo); the decision is
  the event STEP_RESOLVED with `decision` `done` or `retry`. `done` finishes the step through the
  ordinary path, so its `ensures` is checked against the output the operator gave. A `retry` is
  started at once, even if all slots are busy: it is a person's decision, and it exceeds the limit
  of parallel steps by at most one call per decision.
- A request to cancel the run cancels an UNCERTAIN step (it is not running); a failure elsewhere
  that stops the run does the same. A run is not finished while a step is UNCERTAIN.
- The report shows an UNCERTAIN step with its code and message, and the run as WAITING when nothing
  else is running (exit code 3 of the command line).
- A server that starts takes up the runs it owns without a command (api-spec section 5). Command
  line: `flowstat resume RUN` takes up a run (all of the above; `--decision` gives the answer
  a waiting approval awaits) and `flowstat resolve RUN STEP --done [--output JSON]` or `--retry`
  decides about an uncertain step. `resolve` refuses while steps are still to be repeated: `resume`
  comes first. The run ends with the same exit codes as `run`.
- An action that is not registered counts as not idempotent, and so does a registered one that does
  not say it is: the safe default is to ask a person.

`http.call` (`url`, optional `method` default POST, `body`, `timeout` in seconds default 5; http and https only) classifies the answer for the retry policy: a transport failure, a timeout and a 5xx answer are transient (E-ACT-008), a 4xx answer is permanent (E-ACT-010); a bad argument is E-ACT-009. It is registered as idempotent on the promise that the receiver honours the key: the engine can send the key, it cannot make the receiver keep it. The tests use a receiver that does and one that does not (the effect then exists twice after a lost answer). **The hosts it may reach** (US-39, M9). A process is started with a list of allowed hosts: `--allow-host HOST` on `python -m flowstat.serve` (once for each) or `FLOWSTAT_ALLOWED_HOSTS=a.kg,b.kg` in the environment of the core, of a service and of the command line. A service gets the core's list in its environment when it is deployed, and keeps it until it is deployed again. With no list any host may be called, as before; there is no way to write an empty list. A host is matched exactly, without case, port, final dot or user: `https://api.example.kg@evil.example/` is a call to `evil.example`. Two checks use the same reading of a URL: (1) a `url` that is a constant, or whose text begins with a constant that ends the host (`https://api.example.kg/v1/${inputs.quarter}`), must name a host of the list, or the version has E-TYPE-007 and cannot be published; (2) when a call is made, the URL as rendered must name a host of the list, or the step fails with E-ACT-011 before anything is sent. E-ACT-011 is permanent: a retry would be refused the same way. A redirect is a second request to an address the workflow did not write, so it is followed only to a host of the list (E-ACT-011 otherwise). Not covered: a name that resolves to an address inside the network (the list is of names, not of addresses; the names of the list must be the ones the network trusts), and wildcards (`*.kg` is not a host).

Limits, stated: exactly-once delivery is impossible without the cooperation of the receiving system,
so for an action that ignores the key the guarantee is "at most once automatically, otherwise
UNCERTAIN"; a clock jump is not part of the fault model; one process owns a run at a time (the owner
switch is C5, M8).

### 5.6 The time limit of a wait (M9)

`timeout: T` on a step whose action waits for a person (`approval.wait`) fails the step when nobody
answered in `T` seconds. Not to be confused with the argument `timeout` of `http.call`, which is
written under `with` and limits one request.

- **The deadline** is the recorded time of the event `STEP_WAITING` plus `T`. It is in the log, so
  a process that restarts finds the same deadline, and a restart does not give the step more time.
  Time spent while the process was down counts.
- **Who looks.** Nothing stays in memory while a step waits, so the check is made from the log: when
  a core or a service starts (after the runs left going are taken up, api-spec section 5) and then
  every few seconds (`--expire-interval`, default 5; 0 looks only at start). A run is rebuilt only if
  its version has a step with a limit; that is learnt once for each version.
- **The failure** is an ordinary failure of the step with the code `E-RUN-009` ("nobody answered
  within T seconds"), recorded at the time the check noticed it and not at the deadline: a log does
  not get events in the past. The `on_failure` of the step then applies (`fail`, `continue` or
  `compensate`), `retry` never does (a time limit is not transient), and its join rule sees a failed
  step as it sees any.
- **A late answer.** An answer that comes after the deadline and before the check is refused (409
  `E-API-003`, "the answer came too late"); the check fails the step. There is never a moment at
  which both an answer and a time limit are accepted.
- **At save.** `timeout` on a step whose action does not wait is E-TYPE-006 (the type phase: it
  needs the signature of the action). An action the checker does not know is not judged.
- **Migration.** A run whose step is waiting cannot move to a version that changes or removes
  that step's `timeout` (E-MIG-004): the time already waited was counted under the old limit.

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
| E-INPUT-006 to E-INPUT-008 | error | value of a run input outside `values`, below `min`, above `max` | input validation |
| W-BRANCH-001, W-BRANCH-002, W-DEAD-001 | warning | overlapping or uncovered branches, a step that can never run | analysis (M4), see 8.4 |
| I-BRANCH-003 | info | a branch group the analysis cannot decide | analysis (M4), see 8.4 |
| E-VER-001 | error | the `version` in the text differs from the version number being published | service |
| E-VER-002 | error | the `workflow` name in the text differs from the name it is saved under | service |
| E-RUN-001 to E-RUN-008 | error | runtime error of an expression, see 3.1 (the step FAILS) | engine |
| E-RUN-009 | error | nobody answered a waiting step within its `timeout` (the step FAILS), see 5.6 | engine |
| E-CONTRACT-001 | error | `ensures` is false after the action returned (the step FAILS) | engine |
| E-ACT-001 | error | the action is not registered (the step FAILS) | actions |
| E-ACT-002 | error | a file cannot be read; the message has the path as written in `with` | actions |
| E-ACT-003 | error | the file is not a valid form (not an xlsx file, or the first row is not `code, name, value`) | actions |
| E-ACT-004 | error | the output of an action does not have its declared schema | actions |
| E-ACT-005 | error | `statguard.validate`: rows break a rule; the message names the first rows | actions |
| E-ACT-006 | error | the target database cannot be opened, or the rows cannot be written | actions |
| E-ACT-007 | error | a path leads outside the data directory | actions |
| E-ACT-009 | error | an argument of the action is missing or has the wrong type (also: `statguard.validate` is given a rule set that does not exist) | actions |
| E-REC-001 | error | the outcome of a non-idempotent step is unknown after a crash (the step is UNCERTAIN) | recovery |
| E-COMP-001 | error | a compensation failed after its retries (the step is COMPENSATION_FAILED) | recovery |
| E-ACT-008 | error | an HTTP call failed in transport, timed out or got a 5xx answer (transient) | actions |
| E-ACT-010 | error | an HTTP call got a 4xx answer (permanent) | actions |
| E-KEY-001 | error | a service does not answer while its key is replaced; the record stays and `POST /deployments/{id}/keys/recover` ends or takes back the rotation (503) | api (M9), api-spec section 7 |
| E-ACT-011 | error | an HTTP call names a host that is not on the list of allowed hosts, or a redirect leads to one (permanent; nothing was sent) | actions (M9) |
| E-ACT-999 | error | an action raised an exception it did not expect (the step FAILS, the run goes on) | engine |
| E-TYPE-001 | error | type mismatch: an operand, a condition, an argument or a function argument has the wrong type | types (M5), see 9 |
| E-TYPE-002 | error | the output of an action has no such field | types (M5) |
| E-TYPE-003 | error | a required argument of the action is missing | types (M5) |
| E-TYPE-006 | error | `timeout` on a step whose action does not wait for a person | types (M9), see 5.6 |
| E-TYPE-007 | error | a constant `url` of `http.call` names a host that is not on the list of allowed hosts | types (M9), see 5.5 |
| W-TYPE-004 | warning | the action does not take this argument | types (M5) |
| W-TYPE-005 | warning | `==` or `!=` between types that have no value in common (the result is always the same) | types (M5) |
| W-AVAIL-001 | warning | a reference may read an output that does not exist (the step can be skipped or can have failed) | availability (M5), see 10 |
| W-AVAIL-002 | warning | a reference needs more elements of a group than the join guarantees | availability (M5) |
| W-GOAL-001 | warning | a goal may be skipped, or the goals do not cover every case | availability (M5) |
| I-AVAIL-003 | info | the analysis could not decide a reference, a group or a goal (a condition is outside what it reads); nothing is claimed | availability (M5), see 10.2 |
| W-COMP-001 | warning | a step with side effects has no `compensate` in a workflow that undoes failed runs | availability (M5), see 5.4 |

The parser does not stop at the first error: it inserts an error node and continues at the next
synchronisation token (`)`, `]`, `,`, `and`, `or`, end of the string), so one run reports all
syntax errors of a document.

## 7. Open points for M1

- ~~Escape sequences in strings~~ Closed 3 November: `\n \t \r \\ \' \"` are the escapes; any other escape is E-EXPR-004 and the character is kept.
- ~~Whether `in` takes a list literal (`x in [1, 2]`) in M1 or only a catalogue list.~~ Closed 5 November (M4): it does, a list literal is a primary (section 3).
- ~~Exact `retry` semantics when `ensures` fails~~ Closed 5 November: the step fails at once and is not retried (section 5.3).

## 8. Analysis of branches, dead steps and test inputs (M4)

### 8.1 Branch groups

A branch group is a set of at least two steps that

- have a `when`,
- have the same `needs`,
- have guards over the same set of variables,
- are not instances of one `foreach` step.

Guards over different variables are independent choices, not alternatives, and are not compared. A
guarded step that is alone in its group is an optional step and gets no coverage warning. This is
a heuristic: it recognises the shape of `publish` and `return_to_regions` and is wrong for a group
that is meant to be partial; such a group is silenced by writing the guard of the missing case.

For a group the analysis reports, with the diagnostics of 8.4:

- **overlap**: two members whose guards can hold at once, with a witness (the two steps then both
  run);
- **uncovered**: a case that no member handles, with a witness. A case such as `escalated` is
  reported by name.

Both are reported only when proved (`sat`). When a group contains an opaque atom nothing is
claimed and a note says that the group could not be analysed.

### 8.2 Dead steps

A step is dead when its guard is `unsat` over the domains of its variables. This includes a guard
that contradicts an input constraint (`values`, `min`, `max`). That an ancestor is dead or always
skipped, so the step never runs, is the optimistic-run condition of C2 (M5); M4 reports a guard
that is unsatisfiable by itself. Input constraints:

- `values: [..]` lists the allowed constants (the type of each is the type of the input);
- `min` and `max` bound an `int` or `float` input, inclusive;
- they are checked when a run starts: E-INPUT-006 (not one of `values`), E-INPUT-007 (below `min`),
  E-INPUT-008 (above `max`).

### 8.3 Generated test inputs

A target is "step `s` runs" or "step `s` is skipped", for each guarded step. A test case is

- values for the variables that guards read,
- the outputs that mocked actions return (an output field that no guard reads gets the default of
  its type: `0`, `0.0`, `false`, `""`, an empty list),
- the inputs that no guard reads, given by the caller (`--input`); the solver does not generate
  text that matches a pattern.

For each target the solver gives a witness of its run condition. The run condition of a step is
exact: its own guard, and the join rule of the step applied to the run conditions of the steps it
needs (`all_success` is a conjunction, `any_success` a disjunction, `all_done` is always true,
`at_least` a disjunction of conjunctions over the combinations; more than 64 combinations make
the condition opaque). A target whose condition is `unsat` is proved infeasible ("cannot happen").
The candidates are the witnesses of all targets and, for each witness, the cases that differ from
it in one variable only: every value of an enumeration, every constant of a guard and its
neighbour (`±1` for an integer, `±1/2` for a real), and the declared bounds; at most 600 in all.

What a candidate really covers is decided by `predict` (`analysis/predict.py`), which is
independent of the solver's run condition: an optimistic run of the plan that applies the four
join rules (5.1) to concrete values and decides guards with the predicate of the solver. It says
that a step runs, is skipped, or that it cannot tell (an opaque guard, or a guard that reads a
step that did not run); only a decided answer counts as covering a target.

The test set is a set cover of the targets by candidates: first greedy (the case that covers
most still uncovered targets, ties broken by the order of the plan, so it is deterministic), then
an exact branch-and-bound search for a smaller cover within a fixed budget. A target that no
candidate covers and that is not proved infeasible is "no input found"; nothing more is claimed
for it. The two lists are printed by the command; they are not diagnostics, since they come from
a request and not from a draft.

The command `flowstat tests NAME [VERSION] --db DB --input ...` prints the cases, runs each of
them through the simulation of the engine with mocked actions (an action that waits for a person
is answered with the decision of the case) and reports every step whose observed state (run,
skipped, or anything else) differs from the prediction. A contract (`ensures`) that fails on a
mocked output shows up as such a difference. The command ends with status 1 when there is a
difference or when the inputs do not validate, and with 0 otherwise.

Limits: the instances of a `foreach` step are not targets; text that must match a `pattern` is
not generated; a guard with an opaque atom can be run but never decided, so its targets are
reported as "no input found" unless another case happens to decide them.

### 8.4 Diagnostics of the analysis

| Code | Severity | Meaning |
| --- | --- | --- |
| W-BRANCH-001 | warning | two steps of a branch group can both run (the witness is in the message) |
| W-BRANCH-002 | warning | a case that no step of a branch group handles (the witness is in the message) |
| W-DEAD-001 | warning | the guard of a step can never be true |
| I-BRANCH-003 | info | a branch group could not be analysed (an opaque atom, or too many clauses) |

Warnings and notes come from the analysis and do not block `publish`; only errors do. The analysis
runs after a successful compilation, so it never sees a plan with errors. The service returns them
apart from the errors (`SaveResult.findings`, `WorkflowService.findings`), and the command
`publish` prints them before it says "published". What an action's output holds is declared by
the action (`Registry.signatures()`); with no signatures every output is opaque and a group is
reported as not analysed.


## 9. Types of expressions and arguments (M5)

The check runs after a successful compilation, in the analysis (`analysis/types.py`), because it
needs the declared interface of the actions, which the compiler does not know (the layer rule of
`architecture.md`). Its errors are errors of the document: they come back in `diagnostics`, block
the publication, and the warnings come back in `findings` as in 8.4. A foreach step is checked
once, through its first instance, and reported at the position of the source text.

### 9.1 The type of a reference

- `${inputs.x}` has the declared type of the input (`list<string>` for a list input).
- `${step.f}` has the type that the action of the step declares for the field `f`. An action that
  declares its output (the registry does) and has no such field gives E-TYPE-002. An action
  without a declared output (`approval.wait`) gives `string` for the fields of its `choices` and
  `any` for the others.
- `${step[var].f}` is the same for one instance; `${step[*].f}` is `list<type of f>`.
- A path continued after the reference (`${check[*].rows}[0].code`) walks the type: an index
  needs a list and gives its element type, `.name` needs a record that has the field.

### 9.2 Operators and functions

An error is reported only where the run would fail (3.1) or where the declared type of an
argument is broken. The result of an operator whose operands are `any` is `any`.

| Operator | Operands | Result |
| --- | --- | --- |
| `+ - *` | numbers | `int` if both are `int`, else `float` |
| `+` | two strings | `string` |
| `/` | numbers | `float` |
| `%` | numbers | as `+` |
| unary `-` | a number | the same |
| `< <= > >=` | two numbers, two strings, or two dates | `bool` |
| `== !=` | any two | `bool`; W-TYPE-005 when the two types have no value in common (`string` and `int`, a `list` and a number) |
| `and or not` | booleans | `bool` |
| `x in y` | `y` a `list<T>` (any `x`), a `string` (`x` a string) or a record (`x` a string) | `bool` |

Built-in functions: `len(list | string | record)` gives `int`; `sum(list<number>)` gives the element
type; `min` and `max` take `list<number>` or `list<string>` and give the element type;
`count(list)` gives `int`; `exists(reference)` gives `bool`. `when` and `ensures` must be `bool`.

### 9.3 Arguments

A value in `with` is a literal, a template or a whole reference. A whole reference keeps its type;
a template with text has type `string` and each reference in it must be a string, a number, a
boolean or a date (a list, a record or `null` is a run-time error, 3.1); a YAML list or mapping
has the type of its items (`list<any>` when it is empty). The type of the value must fit the
declared type of the argument (E-TYPE-001, at the reference or at the step when the value has no
position of its own). A required argument that is missing is E-TYPE-003, an argument the action
does not take is W-TYPE-004. An action that is not known to the checker (no signature) is not
checked.

## 10. Availability of outputs, groups and goals (M5)

### 10.1 What is guaranteed

Let `ran(Y)` be the condition, over the inputs and the outputs that guards read, under which `Y`
runs when no action fails (the optimistic run; in the code it is the exact run condition of 8.3).
`Av(Y, X)` is a condition under which "if `Y` starts, then `X` has SUCCEEDED". It is sufficient,
never necessary: where the analysis cannot tell it says less, not more. For the predecessors
`P_1 ... P_n` of `Y` let `G_i(X)` be true when `X` is `P_i` and `Av(P_i, X)` otherwise.

- `all_success`: `Av(Y, X) = G_1(X) or ... or G_n(X)`. `Y` starts only when all of them
  succeeded, so one of them is enough.
- `any_success` and `at_least`: `Av(Y, X) = and over i of (S_i or G_i(X))`, where `S_i` is
  `not ran(P_i)`: a predecessor that does not run cannot be the one that succeeded, and one that
  runs may or may not have, so it must pass the fact on. (`ran` is used and not the guard of
  `P_i` alone, so that a predecessor behind a skipped step counts as absent too.)
- `all_done`: the join says nothing about the dependencies; `Av(Y, X)` comes only from the next
  paragraph.

A step `X` with no `on_failure: continue` at or above it has SUCCEEDED exactly when it ran (a
failure of another step stops the run before `Y` is decided, 5.1). For such an `X`, `ran(X)` is a
fact of every descendant. This is what makes "the guard of `c` implies the guard of `approve`" a
reason for no warning (US-21) even when the join of `c` says nothing.

A predecessor with `on_failure: fail` that fails stops the run before `Y` is decided (5.1), so
it does not weaken the conditions. Past the clause cap of the solver, or on an opaque guard, a
condition is taken as false: the analysis keeps fewer facts, never invented ones.

### 10.2 References

For every reference in `with`, `when` or `ensures` of `Y` to the output of `X` (not `Y` itself):

- in `with` and `ensures`, the reference is valid when `when(Y)` entails `Av(Y, X)` (an entailment
  query to the solver); otherwise W-AVAIL-001 with a value that shows it ("`approve` is skipped
  when `decision == 'rejected'`"). When the solver cannot decide (a condition outside what it
  reads), the analysis claims nothing and gives the note I-AVAIL-003 instead;
- in `when`, `when(Y)` is not an assumption (it is what is being computed), so the reference must
  be valid with the assumption `true`, except that a conjunct `exists(reference)` on the left of
  an `and` proves the reference to the right of it.

A reference to a step through `[*]` also needs a number of elements (10.3).

### 10.3 Groups

The elements of `x[*].f` are the instances of `x` that SUCCEEDED, in declaration order. The
number that is guaranteed when `Y` starts is, for the predecessors that are instances of `x`:
`n` for `all_success`, `k` for `at_least: k`, `1` for `any_success`, `0` for `all_done`. A
reference needs `i + 1` elements when it indexes the list with a literal `i`, and one element as
the argument of `min` or `max`. W-AVAIL-002 when the need exceeds what is guaranteed.

### 10.4 Goals and dead steps

For `goals: any_of`, W-GOAL-001 when the conditions `ran(g)` of the goals do not cover every case
(the solver's exhaustiveness query, with an input that shows it); for `all_of`, once per goal that
is not guaranteed to run. A goal whose `ran` is unsatisfiable is dead (8.2), and so is every step
whose `ran` is unsatisfiable (an ancestor is dead or always skipped): W-DEAD-001 names the first
dead ancestor. A step that is dead because of its own guard is reported by 8.2, so the message
here appears only for the steps behind it, and for a quorum that cannot be reached. The optimistic run ignores failures; a goal that fails is a failure of the run
and not a skipped goal.

### 10.5 Side effects without an undo

A step whose action is declared to have an effect outside the engine (the registry says so:
`db.write`, `email.send`, `http.call`) and that has no `compensate` gets W-COMP-001 when some
step of the workflow has `on_failure: compensate`: a failed run will leave it as it is.

## 11. Priority by the feasible critical path (M5)

The priority of a ready step (5.1) is the length of the longest feasible chain that starts at it:
a sequence of steps, each an ancestor of the next, that can all run in the same execution. A
skipped step costs nothing, so a chain may pass over it. The chain is feasible when the solver
finds the conjunction of the `ran` conditions of its steps satisfiable. Durations are the
averages as before. When the inputs of the run are known they are fixed in the conditions
(a guard on an input is then decided), so the priority is that of this run. The number of
alternative chains kept at a step is capped (16). When more are left, the longest one that is
dropped stays under the weaker condition of its first step, so a cut makes a priority longer,
never shorter. An opaque atom counts as true, and so does a condition too large to keep
(more than 300 nodes). Every priority is therefore an upper bound of the chain that really runs
in any execution, and the plain longest path is an upper bound of the priority.
