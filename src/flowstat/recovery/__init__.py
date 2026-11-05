"""C4 fault tolerance. Layer 6.

wal.py        write-ahead log: the events of a scheduler step are stored before its commands run.
saga.py       the order of a compensation: which undos must precede which (dependency bitsets).

The rebuilding of a run from its history (`Scheduler.restore`) and the decision about a step that
was running at the crash (repeat it with the same key, or UNCERTAIN) live in `engine/scheduler.py`,
because they need the private state of the scheduler and the engine layer cannot import this one;
which actions are idempotent is told to it by the registry of actions. The crash-injection suite is
`tests/crash/`.

Built in I3. Algorithm card: docs/algorithms/C4.md.
"""
