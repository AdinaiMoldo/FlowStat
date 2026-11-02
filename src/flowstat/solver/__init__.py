"""C6 predicate solver and test generator. Layer 2.

Decides satisfiability, exclusivity and exhaustiveness of branch conditions without Z3.

predicate.py  normal forms (negation normal form, DNF with a clause cap).
domains.py    per-variable domains: interval sets, enum sets, booleans.
diffgraph.py  difference graph and Bellman-Ford with strict edges.
witness.py    witness values from shortest-path distances.
cover.py      greedy set cover and the test generator.

Built in I4. Algorithm card: docs/algorithms/C6.md.
"""
