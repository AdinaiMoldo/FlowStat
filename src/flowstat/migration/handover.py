"""Ownership handover behind an epoch fence (C5, level 2).

A run moves from one owner to another in three calls: fence the old owner, import at the new one,
release the old one. Whatever stops the process between two calls, ``recover`` takes the run
forward from what the two owners say; it never goes back, so there is no moment from which the
run could have two owners or none that a restart cannot repair. The owners are behind a protocol:
this module knows nothing of databases or HTTP, and the crash suite puts a fault in front of
every call.

The history of the old owner gets no event (that would use a sequence number the copy at the core
already has); the ownership is a table at each owner (``repo/ownership.py``), and the new owner
writes one event, RUN_MIGRATED, after the imported history.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from flowstat.model.events import Event, event_from_dict, event_to_dict
from flowstat.model.ownership import Ownership, OwnerState


class OwnerUnavailableError(RuntimeError):
    """An owner could not be reached (or answered with a failure): try again later."""


class InconsistentOwnershipError(RuntimeError):
    """The owners say something that no sequence of the protocol can produce."""


@dataclass(frozen=True, slots=True)
class Plan:
    """One handover. ``epoch`` is the epoch of the old owner before the move; the new owner
    holds ``epoch + 1``. ``last_seq`` is the last event the check saw: the fence refuses if the
    history has grown since. ``inputs`` are the inputs as the new version reads them."""

    run_id: str
    source: str
    target: str
    epoch: int
    version: int
    inputs: Mapping[str, object]
    last_seq: int
    at: float

    def to_json(self) -> str:
        return json.dumps(
            {
                "run_id": self.run_id,
                "source": self.source,
                "target": self.target,
                "epoch": self.epoch,
                "version": self.version,
                "inputs": dict(self.inputs),
                "last_seq": self.last_seq,
                "at": self.at,
            },
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, text: str) -> Plan:
        d = json.loads(text)
        return cls(
            d["run_id"], d["source"], d["target"], d["epoch"], d["version"],
            d["inputs"], d["last_seq"], d["at"],
        )  # fmt: skip


@dataclass(frozen=True, slots=True)
class Snapshot:
    """What an owner hands over: the run as it is stored there."""

    run_id: str
    workflow: str
    version: int
    inputs: Mapping[str, object]
    created_at: str
    events: tuple[Event, ...]
    ownership: Ownership

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "workflow": self.workflow,
            "version": self.version,
            "inputs": dict(self.inputs),
            "created_at": self.created_at,
            "events": [event_to_dict(e) for e in self.events],
            "ownership": {
                "run_id": self.run_id,
                "epoch": self.ownership.epoch,
                "state": self.ownership.state.value,
                "peer": self.ownership.peer,
                "version": self.ownership.version,
            },
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, object]) -> Snapshot:
        o = d["ownership"]
        assert isinstance(o, dict)
        run_id = str(d["run_id"])
        return cls(
            run_id,
            str(d["workflow"]),
            int(d["version"]),  # type: ignore[call-overload]
            dict(d["inputs"]),  # type: ignore[call-overload]
            str(d["created_at"]),
            tuple(event_from_dict(e) for e in d["events"]),  # type: ignore[attr-defined]
            Ownership(run_id, int(o["epoch"]), OwnerState(o["state"]), o["peer"], o["version"]),
        )


class Owner(Protocol):
    """A store that can own runs. Every call is repeatable: the same call again answers the same."""

    def ownership(self, run_id: str) -> Ownership: ...

    def export(self, run_id: str) -> Snapshot: ...

    def fence(self, plan: Plan) -> Ownership:
        """OWNED(epoch) -> MIGRATING(epoch + 1) toward the target, if nothing was added after
        ``last_seq`` and no call is in flight."""
        ...

    def adopt(self, snapshot: Snapshot, plan: Plan) -> Ownership:
        """Store the run and its history, write RUN_MIGRATED, become OWNED at epoch + 1."""
        ...

    def release(self, plan: Plan) -> Ownership:
        """MIGRATING(epoch + 1) -> MIGRATED_OUT."""
        ...

    def retarget(self, plan: Plan) -> Ownership:
        """Source and target are the same store: change the version in one transaction."""
        ...


class Journal(Protocol):
    def begin(self, plan: Plan) -> None: ...

    def pending(self) -> list[Plan]: ...

    def end(self, run_id: str) -> None: ...


class Mirror(Protocol):
    def moved(self, plan: Plan) -> None:
        """Tell the core's copy of the run where it is now: owner, version and inputs, so that
        what the new owner ships to the core still describes the same run."""
        ...


def move(plan: Plan, owners: Mapping[str, Owner], journal: Journal, mirror: Mirror) -> Ownership:
    """Hand the run over. Returns the ownership of the new owner."""
    if plan.source == plan.target:
        return owners[plan.source].retarget(plan)
    journal.begin(plan)
    owners[plan.source].fence(plan)
    return _forward(plan, owners, journal, mirror, exported=None)


def _forward(
    plan: Plan,
    owners: Mapping[str, Owner],
    journal: Journal,
    mirror: Mirror,
    exported: Ownership | None,
) -> Ownership:
    """From a fenced old owner (or an imported run, ``exported`` = the new ownership) to the end."""
    source, target = owners[plan.source], owners[plan.target]
    mine = exported
    if mine is None:
        mine = target.adopt(source.export(plan.run_id), plan)
    source.release(plan)
    mirror.moved(plan)
    journal.end(plan.run_id)
    return mine


def recover(plan: Plan, owners: Mapping[str, Owner], journal: Journal, mirror: Mirror) -> str:
    """Take a handover that was interrupted forward. Returns what became of it: ``completed``,
    ``abandoned`` (the fence was never taken, nothing had changed) or ``unreachable`` (an owner
    does not answer; the journal keeps the plan)."""
    source, target = owners[plan.source], owners[plan.target]
    try:
        was, now = source.ownership(plan.run_id), target.ownership(plan.run_id)
        new_epoch = plan.epoch + 1
        if now.owned and now.epoch >= new_epoch:  # imported (and maybe moved on since)
            if was.epoch == new_epoch:
                source.release(plan)
            mirror.moved(plan)
            journal.end(plan.run_id)
            return "completed"
        if was.state is OwnerState.MIGRATING and was.epoch == new_epoch and was.peer == plan.target:
            _forward(plan, owners, journal, mirror, exported=None)
            return "completed"
        if was.owned and was.epoch == plan.epoch:  # the fence was never taken
            journal.end(plan.run_id)
            return "abandoned"
    except OwnerUnavailableError:
        return "unreachable"
    raise InconsistentOwnershipError(
        f"{plan.run_id}: {plan.source} says {was}, {plan.target} says {now}"
    )


def recover_all(owners: Mapping[str, Owner], journal: Journal, mirror: Mirror) -> dict[str, str]:
    """Recover every handover in the journal; the result is the outcome for each run."""
    result: dict[str, str] = {}
    for plan in journal.pending():
        try:
            result[plan.run_id] = recover(plan, owners, journal, mirror)
        except KeyError:  # an owner that no longer exists in the registry cannot answer
            result[plan.run_id] = "unreachable"
    return result
