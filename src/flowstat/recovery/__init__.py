"""C4 fault tolerance. Layer 6.

wal.py        write-ahead event log: an event is written before it is acknowledged.
replay.py     state reconstruction by replay.
rules.py      steps caught in RUNNING at the crash: idempotent re-run, otherwise UNCERTAIN.
saga.py       compensation plan in reverse dependency order, resumable after a crash.
faults.py     fault-injection harness used by tests/crash.

Built in I3. Algorithm card: docs/algorithms/C4.md.
"""
