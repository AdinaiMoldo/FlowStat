"""A bounded memo of compiled plans (US-41, docs/research/plan-cache-measurement.md).

The plan of a published version never changes (docs/dsl-spec.md, DR-03): its text and the snapshot
of the catalogue it reads are frozen. A cache keyed by those two texts therefore cannot go stale,
and a changed text is simply another key. The least recently used key goes first when the cache is
full. It is shared by the threads of one process; the value is made outside the lock, so two
threads that ask at the same time for a key that is not there may both make it, and one of the two
is kept (a plan is a value, so which one does not matter).
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable


class PlanCache[V]:
    def __init__(self, size: int = 64) -> None:
        if size < 0:
            raise ValueError("the size of the cache is not negative")
        self.size = size
        self.hits = 0
        self.misses = 0
        self._items: OrderedDict[Hashable, V] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def get(self, key: Hashable, make: Callable[[], V]) -> V:
        """The value kept for ``key``, or ``make()`` kept for it. An error is not kept."""
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                self.hits += 1
                return self._items[key]
            self.misses += 1
        value = make()
        if self.size:
            with self._lock:
                self._items[key] = value
                self._items.move_to_end(key)
                while len(self._items) > self.size:
                    self._items.popitem(last=False)
        return value
