# Examples

Only synthetic data lives here. Real Committee data is never committed; the department's real processes are rewritten in the DSL without their data.

- `quarterly_consolidation.yaml`: the main example of the Project Proposal (22 steps after expansion).
- `catalogue.yaml`: the catalogue snapshot used by `foreach`.
- `exercise_one_region.yaml`: the reference solution of the usability exercise (`docs/research/nfr06-exercise.md`): one region, five steps. It is not given to the person who takes the exercise.

## Running the example

```text
flowstat publish examples/quarterly_consolidation.yaml --db flowstat.db --catalogue examples/catalogue.yaml
flowstat run quarterly_consolidation --db flowstat.db --data forms --target results.db --input quarter=2025Q1 --decision approved
flowstat report <run id> --db flowstat.db
```

`forms/BIS/2025Q1.xlsx`, `forms/OSC/2025Q1.xlsx`, ... are the regional forms: one sheet, a header row `code, name, value`, then one row per indicator (made-up data only). Without `--decision` the run stops at the approval and the command ends with exit code 3 (WAITING).

