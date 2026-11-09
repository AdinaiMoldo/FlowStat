"""The core takes the events of a deployed service (US-31, docs/api-spec.md section 9).

A service ships the events of its runs in batches, at least once. The core stores a run and its
events with the rules of ``RunStore`` (an event already stored and identical is ignored; a
different one, or a gap, refuses the whole batch), so the pair (run, sequence number) is the key
and a batch that arrives twice changes nothing. Who may ship is decided by the ingest key of the
deployment, which belongs to no role.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from flowstat.model.events import Event, event_from_dict
from flowstat.repo.deployments import Deployment, DeploymentStore
from flowstat.repo.runs import EventConflictError, RunConflictError, RunStore
from flowstat.service.users import hash_token

MAX_BATCH = 200  # events in one batch


class UnknownKeyError(Exception):
    """The key is not the ingest key of any deployment."""


class WrongDeploymentError(Exception):
    """The key is the ingest key of another deployment (or the deployment does not exist)."""


class IngestInvalidError(ValueError):
    """The batch is not one the deployment may ship, or is not a batch."""


class IngestConflictError(RuntimeError):
    """The batch does not fit what the core holds. ``expected`` is the sequence number the core
    needs next when the batch leaves a gap, and None when an event differs from the stored one."""

    def __init__(self, message: str, expected: int | None = None) -> None:
        super().__init__(message)
        self.expected = expected


@dataclass(frozen=True, slots=True)
class RunMeta:
    run_id: str
    workflow: str
    version: int
    inputs: dict[str, object]
    created_at: str


@dataclass(frozen=True, slots=True)
class Batch:
    run: RunMeta
    events: tuple[Event, ...]


def _field(data: object, key: str, kind: type | tuple[type, ...]) -> object:
    if not isinstance(data, dict) or key not in data:
        raise IngestInvalidError(f"the batch needs '{key}'")
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, kind):
        raise IngestInvalidError(f"'{key}' has the wrong type")
    return value


def parse_batch(data: dict[str, object]) -> Batch:
    """Read a batch from plain data (the body of a request)."""
    run = _field(data, "run", dict)
    meta = RunMeta(
        str(_field(run, "run_id", str)),
        str(_field(run, "workflow", str)),
        int(_field(run, "version", int)),  # type: ignore[call-overload]
        dict(_field(run, "inputs", dict)),  # type: ignore[call-overload]
        str(_field(run, "created_at", str)),
    )
    raw = _field(data, "events", list)
    try:
        events = tuple(event_from_dict(e) for e in raw)  # type: ignore[attr-defined]
    except (ValueError, TypeError, AttributeError) as error:
        raise IngestInvalidError(str(error)) from None
    return Batch(meta, events)


class IngestService:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.runs = RunStore(db)
        self.deployments = DeploymentStore(db)

    def authenticate(self, deployment_id: str, token: str) -> Deployment:
        """The deployment ``token`` is the ingest key of, if that is ``deployment_id``."""
        found = self.deployments.by_ingest_hash(hash_token(token))
        if found is None:
            raise UnknownKeyError
        if found.id != deployment_id:
            raise WrongDeploymentError
        return found

    def accept(self, deployment: Deployment, batch: Batch, now: str) -> int:
        """Store the batch; returns the number of the last event the run has."""
        meta = batch.run
        if (meta.workflow, meta.version) != (deployment.workflow, deployment.version):
            raise IngestInvalidError(
                f"the deployment serves {deployment.workflow} v{deployment.version}, "
                f"not {meta.workflow} v{meta.version}"
            )
        if not batch.events:
            raise IngestInvalidError("a batch has at least one event")
        if len(batch.events) > MAX_BATCH:
            raise IngestInvalidError(f"a batch has at most {MAX_BATCH} events")
        try:
            last = self.runs.ingest(
                meta.run_id,
                meta.workflow,
                meta.version,
                meta.inputs,
                meta.created_at,
                deployment.id,
                batch.events,
            )
        except RunConflictError as error:
            raise IngestConflictError(str(error)) from None
        except EventConflictError as error:
            raise IngestConflictError(str(error), error.expected) from None
        self.deployments.touch(deployment.id, now)
        return last
