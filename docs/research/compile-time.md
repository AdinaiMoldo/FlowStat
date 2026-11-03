# Compile time (NFR-01)

Question: does the compiler (phases 0 to 3, with the reader, the shape validator, the parser and
the graph algorithms) stay interactive? Target of NFR-01: 200 steps in at most 1 s; a 5,000-line
file in at most 3 s.

Method: `python benchmarks/bench_compile.py`, median of 5 runs, one machine (cloud container, one
core used), Python 3.13. Workflows are generated: a chain where step i needs step i-1 and reads its
output, and a fan-out with a `foreach` over a catalogue list, a per-item check and a quorum join.

| shape | steps after expansion | lines | median compile time |
| --- | --- | --- | --- |
| chain | 50 | 205 | 28 ms |
| chain | 200 | 805 | 161 ms |
| chain | 1000 | 4005 | 544 ms |
| chain | 2000 | 8005 | 1206 ms |
| fan-out 9 | 19 | 15 | 2 ms |
| fan-out 100 | 201 | 15 | 4 ms |
| fan-out 500 | 1001 | 15 | 17 ms |

Conclusion: both targets are met with a wide margin (200 steps: 161 ms against 1 s; about 5,000
lines: about 0.7 s against 3 s). Time grows roughly linearly with the number of lines, so the cost
is dominated by reading the YAML text, not by the graph algorithms (Tarjan, shortest cycle and
ancestor bitsets are linear in nodes and edges). No optimisation is needed. The test
`tests/unit/compiler/test_performance.py` keeps the thresholds of the requirement in the suite.

Limit: the numbers are from one machine; the thresholds in the test have a factor of about 6 of
margin so that a slower CI machine does not make it flaky.
