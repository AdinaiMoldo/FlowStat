"""One lock per run: a run is driven by one thread at a time (docs/api-spec.md section 5)."""

from __future__ import annotations

import threading


class RunLocks:
    def __init__(self) -> None:
        self._held: set[str] = set()
        self._guard = threading.Lock()

    def try_acquire(self, run_id: str) -> bool:
        with self._guard:
            if run_id in self._held:
                return False
            self._held.add(run_id)
            return True

    def release(self, run_id: str) -> None:
        with self._guard:
            self._held.discard(run_id)

    def busy(self, run_id: str) -> bool:
        with self._guard:
            return run_id in self._held
