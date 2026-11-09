"""A service ships the history of its runs to the core (US-31, docs/api-spec.md section 9).

The log of the service is the source of truth and the core is a copy that catches up. For each
run the service remembers how far the core has acknowledged (``OutboxStore``); a round sends what
lies beyond, in batches, in order. Delivery is at least once: a lost acknowledgement means the
same batch is sent again, and the core ignores events it already has. If the core says it has
less than we believed (it lost data), the position moves back and the missing events are sent.

The transport is passed in (``send``), so this module does no network and a test gives it a
core that is a second database.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from flowstat.model.events import event_to_dict
from flowstat.repo.outbox import OutboxStore
from flowstat.repo.runs import StoredRun

BATCH = 200


class SendFailed(Exception):
    """The core could not be reached (no answer, a broken connection)."""


@dataclass(frozen=True, slots=True)
class Reply:
    status: int
    body: Mapping[str, object] = field(default_factory=dict)


Send = Callable[[dict[str, object]], Reply]  # raises SendFailed


@dataclass(frozen=True, slots=True)
class ShipReport:
    events: int  # events the core acknowledged in this round
    runs: int  # runs whose history the core now has to the end
    skipped: tuple[str, ...]  # runs the core refused for good (a conflict, a bad request)
    stopped: str | None  # why the round ended early (the core is away, or refuses the key)

    @property
    def clean(self) -> bool:
        return self.stopped is None and not self.skipped


class Backoff:
    """The wait between rounds that failed: 1 s, then twice as long, up to 30 s."""

    def __init__(self, first: float = 1.0, cap: float = 30.0) -> None:
        self.first, self.cap = first, cap
        self._next = first

    def next(self) -> float:
        wait = self._next
        self._next = min(self._next * 2, self.cap)
        return wait

    def reset(self) -> None:
        self._next = self.first


_STOP = {401, 403}  # the key is not accepted: nothing else will be either, until it is fixed


class Outbox:
    def __init__(self, db: sqlite3.Connection, send: Send, batch_size: int = BATCH) -> None:
        self.store = OutboxStore(db)
        self.send = send
        self.batch_size = batch_size

    def ship_once(self) -> ShipReport:
        """One round over every run that has events the core has not acknowledged."""
        events = done = 0
        skipped: list[str] = []
        for run in self.store.pending():
            sent, outcome = self._ship_run(run)
            events += sent
            if outcome == "ok":
                done += 1
            elif outcome == "skip":
                skipped.append(run.run_id)
            else:
                return ShipReport(events, done, tuple(skipped), outcome)
        return ShipReport(events, done, tuple(skipped), None)

    def _ship_run(self, run: StoredRun) -> tuple[int, str]:
        """Returns (events acknowledged, "ok" | "skip" | the reason to stop the round)."""
        sent = 0
        moved_back = False
        while True:
            at = self.store.shipped_through(run.run_id)
            batch = self.store.events_after(run.run_id, at, self.batch_size)
            if not batch:
                return sent, "ok"
            payload: dict[str, object] = {
                "run": {
                    "run_id": run.run_id,
                    "workflow": run.workflow,
                    "version": run.version,
                    "inputs": run.inputs,
                    "created_at": run.created_at,
                },
                "events": [event_to_dict(e) for e in batch],
            }
            try:
                reply = self.send(payload)
            except SendFailed as error:
                return sent, f"the core is not reachable: {error}"
            if reply.status == 200:
                self.store.set(run.run_id, batch[-1].seq)
                sent += len(batch)
                continue
            expected = reply.body.get("expected")
            if reply.status == 409 and isinstance(expected, int) and not isinstance(expected, bool):
                if moved_back:
                    return sent, "skip"  # the core keeps asking for what we just sent
                moved_back = True
                self.store.set(run.run_id, expected - 1, allow_back=True)
                continue
            if reply.status in _STOP:
                return sent, f"the core refuses the key ({reply.status})"
            if reply.status >= 500:
                return sent, f"the core failed ({reply.status})"
            return sent, "skip"  # 404, 409 without 'expected', 422: this run will not be accepted


__all__ = ["BATCH", "Backoff", "Outbox", "Reply", "Send", "SendFailed", "ShipReport"]
