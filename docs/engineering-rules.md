# Engineering rules (short form)

Full text: `Engineering_Playbook.docx`, section 2.

1. Every increment ends with a working system.
2. Test before code for C1, C3, C4 and C6.
3. The scope of an increment does not change while it runs.
4. A complex function is not closed without pseudocode, an activity diagram, a sequence diagram, tests and a benchmark or measurement.
5. We do not call something complex if it is a formula or a library call. A function counts as complex only with a non-trivial search or iteration, edge cases and an independent oracle.
6. Decisions are verified by measurement while it is cheap.
7. Limitations are written down at once.
8. Process records (review, retrospective, velocity) are closed the same day as the increment.

## CI gate

A commit does not pass if any test is red, if coverage of C1-C6 is below 90%, if golden output changed without an explicit update of the reference, or if a module of C1-C6 imports a third-party library that is not on the allow-list.

## Branches and commits

`main` is always green. One short branch per story (`us-05-foreach`), the story ID in the commit message (`US-05: expand foreach in phase 0`). A tag at the end of every increment (`i1` ... `i6`).
