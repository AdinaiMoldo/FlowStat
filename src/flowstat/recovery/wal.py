"""The write-ahead log (C4): the events of a scheduler step are stored before its commands run.

The scheduler produces events and commands together. If the commands ran first and the engine
died before the events were stored, an action would have run that the history does not know about.
So the driver calls ``commit`` after every call into the scheduler and only then dispatches the
commands. One commit is one transaction: after a crash the history ends at a scheduler step, never
in the middle of one (a prefix that ends in the middle is still restorable, which the crash
tests check, so the guarantee does not rest on the transaction alone).
"""

from __future__ import annotations

from collections.abc import Sequence

from flowstat.model.events import Event
from flowstat.repo.runs import RunStore


class WriteAheadLog:
    def __init__(self, store: RunStore, run_id: str, stored: int = 0) -> None:
        self._store = store
        self._run_id = run_id
        self._stored = stored  # how many events of the scheduler are in the store

    def commit(self, events: Sequence[Event]) -> None:
        """Store the events that are not stored yet (all of ``events`` are the scheduler's)."""
        fresh = events[self._stored :]
        if not fresh:
            return
        self._store.append(self._run_id, fresh)
        self._stored = len(events)
