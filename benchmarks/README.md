# Benchmarks

Numbers, not impressions. Each benchmark ends with a one-page table and a conclusion in `docs/research/`, even when the conclusion is "no gain".

| Benchmark | Function | Increment | Compares |
| --- | --- | --- | --- |
| compile-time | C1 | I1 | compile time against file size (NFR-01) |
| scheduling | C3 | I2, I4 | FIFO, plain critical path, feasible critical path |
| wal-cost | C4 | I3 | write before acknowledgement against write after (`bench_wal.py`) |
| recovery | C4 | I3 | restart from scratch, plain replay and ours after a crash at every event; three orders of compensation (`bench_recovery.py`) |
| solver | C6 | I4 | DNF with a clause cap against brute force, by predicate size |
