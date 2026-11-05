# Decision journal

One entry per significant decision: date, what we chose, what we rejected, what we measured with.

## 2026-11-02 Repository structure and layering

- Chose: packages by layer (`model`, `dsl`, `solver`, `repo`, `compiler`, `analysis`, `engine`, `actions`, `recovery`, `migration`, `api`, `deploy`); dependencies point down only.
- Enforced by: `tests/architecture/test_layers.py`.
- Rejected: one flat package (a violation of the dependency direction would be invisible).

## 2026-11-02 Algorithms of C1-C6 are written by us

- Chose: our own lexer and recursive-descent parser for expressions, our own document shape validator, our own solver, scheduler and recovery. Third-party: only `ruamel.yaml` for reading YAML.
- Rejected: Lark (expression parsing), jsonschema (shape check), Z3 and sympy (solving), workflow frameworks.
- Enforced by: `tests/architecture/test_allowed_imports.py` and the allow-list in `reuse.md`.

## 2026-11-02 Feasible critical path

- Chose: priority of C3 from the longest chain of steps that can run in the same execution, decided by the solver of C6 (built in I4). I2 ships the plain longest path as the baseline.
- Measured with: benchmark FIFO against plain against feasible (to be done in I4).

## 2026-11-02 Two levels per C function, nine milestones

- Chose: every C function has two levels (a known algorithm with our transformation, then an algorithm built on its result); see `docs/algorithms/README.md`. I4 and I5 are split into two milestones each, and a hardening milestone M9 is added.
- Rejected: replacing C5 with write-conflict analysis (it would change DR-01, US-32/33 and the diagrams, and overlaps C2/C6). C5 stays; in M8 conformance is built before handover.
- Reason for the split: smaller milestones (about 20 to 30 SP) lower the risk of a late surprise.

## 2026-11-03 Working practice follows FleetSync

- Chose: the plan of the Project Proposal stays as it is; the way of working (goal, readiness audit, journal, research notes, review and retrospective per milestone, verification pass against the code) follows the FleetSync project. See `process.md`.

## 2026-11-05 Published versions are protected by the database

- Chose: SQLite triggers refuse UPDATE and DELETE on a published row; the store also refuses to publish twice. A published version keeps the catalogue snapshot it was compiled against, and its plan is always rebuilt from that snapshot.
- Rejected: protection only in code (a bug above the store could then change a published version without anyone noticing).
- Checked with: a test that runs raw SQL UPDATE and DELETE against a published row.

## 2026-11-05 The version number is part of the document

- Chose: publishing fails (E-VER-001) when the `version` in the text differs from the version number; the same for the workflow name (E-VER-002).
- Rejected: the service rewriting the designer's text. The text the designer wrote must be the text that is stored.
- Cost: after a publication the designer edits the `version` line of the next draft.

## 2026-11-05 A missing colon is reported where the key starts

- Found by: the acceptance test of US-03 (criterion: missing colon on line 14 gives an error on line 14). The YAML library notices the problem one line later.
- Chose: for this error the reader uses the position of the start of the key. Other YAML errors keep the position given by the library.

## 2026-11-05 Recount of story points

- Chose: keep 203 SP as the zero point of the burndown and show the added scope separately: parser and shape validator +6 SP (M1, delivered), feasible critical path +5 SP (M5, estimated). Planned total 214 SP.
- Rejected: changing the baseline (the burndown would then hide that the plan grew).
- Where recorded: `backlog.md`, section 3 of the Playbook and the planning paragraph of the Project Proposal.

## 2026-11-05 Process wording and documents brought in line

- The sentence in section 7 of the Project Proposal said ceremonies make no sense for one person, while section 11.5 keeps a minimal review and retrospective. Section 7 now refers to section 11.5.
- The Playbook names the nine regional offices (seven oblasts plus Bishkek and Osh) at its first mention; the catalogue keeps calling them regions.
- The copies of both documents in `docs/` are refreshed from the current text; the document properties carry only the author and the title.

## 2026-11-05 M2 starts: evaluation semantics before code

- Chose: strict, small semantics (booleans for `and`/`or`/`not`, no implicit conversions, `exists` as the only guard) written in `dsl-spec.md` 3.1 before the evaluator. A fan-in reads the instances that succeeded, in declaration order.
- Rejected: Python truthiness (a number in a `when` would silently work and hide a mistake), and `null` for a missing field (a typo in a field name would silently make a condition false).
- Checked with: the evaluator is compared with Python on random expressions (same value on every case in the common fragment) and with golden cases for every error code.

