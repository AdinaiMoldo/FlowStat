"""C2 availability analysis and feasible critical path. Layer 4.

Path-sensitive analyses over the plan.

guarded.py    guarded facts and transfer functions per join rule (C2).
ran.py        the optimistic-run condition of every step.
feasible.py   feasible critical path: longest chain whose steps can run together (uses solver).
scope.py      completion scope served to the editor.

Built in I4. Algorithm cards: docs/algorithms/C2.md, C3.md.
"""
