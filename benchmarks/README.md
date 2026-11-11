# Benchmarks

Numbers, not impressions. Each benchmark ends with a one-page table and a conclusion in `docs/research/`, even when the conclusion is "no gain".

| Benchmark | Function | Increment | Compares |
| --- | --- | --- | --- |
| compile-time | C1 | I1 | compile time against file size (NFR-01) |
| scheduling | C3 | I2, I4 | FIFO, plain critical path, feasible critical path |
| wal-cost | C4 | I3 | write before acknowledgement against write after (`bench_wal.py`) |
| recovery | C4 | I3 | restart from scratch, plain replay and ours after a crash at every event; three orders of compensation (`bench_recovery.py`) |
| solver | C6 | I4 | DNF with a clause cap against brute force, by predicate size; generator against one case per target and random inputs (`bench_solver.py`) |
| api | B4, B5 | I5a | cost of a request, of listing runs, of a pool of threads and of a process deployment (`bench_api.py`) |
| m7 | A2, A6, B3 | I5b | cost of a batch of events at the core, of shipping, of the graph function and of its drawing (`bench_m7.py`) |
| deep-chains | C2 | M9 | availability and priorities on a chain of 100 to 2,000 guarded steps, with and without the bound on the size of a run condition (`bench_chain.py`) |
| migration | C5 | I6 | cost of the conformance check per history, and (US-33) the handover against stop-and-restart, and the counting of statistics (`bench_migration.py`) |
| plan-cache | B4 | M9 | cost of a report, a graph, a list and a start with the plan compiled every time against kept (`bench_plan_cache.py`) |
