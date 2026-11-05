"""C6 predicate solver. Layer 2.

Decides satisfiability, exclusivity, entailment and exhaustiveness of branch conditions without
Z3. Every answer is ``SAT`` (with a witness), ``UNSAT`` (a proof) or ``UNKNOWN``.

predicate.py  the predicate tree, negation normal form, DNF with a clause cap, three-valued
              evaluation.
domains.py    per-variable domains: interval sets over exact numbers, string sets, booleans.
diffgraph.py  difference constraints: Bellman-Ford with strict edges, exact witness values.
solve.py      ``check`` (one clause at a time: domains first, then the difference graph) and the
              questions built on it: ``overlap``, ``uncovered``, ``entails``.
lower.py      from an expression of the DSL to a predicate; what is outside the fragment becomes an
              opaque atom.

The branch analysis and the test generator are in ``analysis`` (layer 4): they need the plan.
Algorithm card: docs/algorithms/C6.md. Fragment and semantics: docs/dsl-spec.md 3.2.
"""
