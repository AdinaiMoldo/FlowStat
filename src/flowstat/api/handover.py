"""The routes by which the core moves a run into and out of a deployed service (api-spec 10).

Each route is one call of the handover protocol (``migration/handover.py``) on the runs of this
service's database. They need the permission ``handover:control``, which only the control key of
the deployment holds; the keys that start runs and answer approvals do not.
"""

from __future__ import annotations

import sqlite3

from fastapi import Depends, Request

from flowstat.api.deps import get_db
from flowstat.api.errors import fail
from flowstat.api.models import AdoptIn, OwnershipOut, PlanIn, SnapshotBody
from flowstat.api.secured import SecuredRouter
from flowstat.migration.handover import Plan, Snapshot
from flowstat.model.ownership import Ownership
from flowstat.repo.ownership import EpochMismatchError, NotOwnerError, RunChangedError
from flowstat.repo.runs import EventConflictError, RunNotFoundError
from flowstat.service.handover import LocalOwner, RunBusyError

router = SecuredRouter(tags=["handover"])


def ownership_out(o: Ownership) -> OwnershipOut:
    return OwnershipOut(
        run_id=o.run_id, epoch=o.epoch, state=o.state.value, peer=o.peer, version=o.version
    )


def plan_of(body: PlanIn) -> Plan:
    return Plan(
        body.run_id, body.source, body.target, body.epoch, body.version, body.inputs,
        body.last_seq, body.at,
    )  # fmt: skip


def owner_of(request: Request, db: sqlite3.Connection) -> LocalOwner:
    return LocalOwner(db, request.app.state.locks)


def refuse(error: Exception, run_id: str) -> Exception:
    """The answer for a call that the state of the run does not allow."""
    if isinstance(error, RunNotFoundError):
        return fail(404, "E-API-002", f"there is no run {run_id}")
    if isinstance(error, NotOwnerError | EpochMismatchError):
        return fail(409, "E-MIG-010", str(error))
    if isinstance(error, RunChangedError):
        return fail(409, "E-API-003", f"the history of {run_id} changed; check it again")
    if isinstance(error, RunBusyError):
        return fail(409, "E-API-003", str(error))
    if isinstance(error, EventConflictError):
        return fail(409, "E-API-003", str(error))
    return error


def same_run(path_id: str, given: str) -> None:
    if path_id != given:
        raise fail(422, "E-API-001", "the run in the body is not the run in the path", "run_id")


_EXPECTED = (
    RunNotFoundError,
    NotOwnerError,
    EpochMismatchError,
    RunChangedError,
    RunBusyError,
    EventConflictError,
)


@router.secured(
    "GET", "/migration/{run_id}/ownership", "handover:control", summary="Who owns the run here"
)
def ownership(
    run_id: str, request: Request, db: sqlite3.Connection = Depends(get_db)
) -> OwnershipOut:
    return ownership_out(owner_of(request, db).ownership(run_id))


@router.secured(
    "GET", "/migration/{run_id}/export", "handover:control", summary="The run as stored here"
)
def export(run_id: str, request: Request, db: sqlite3.Connection = Depends(get_db)) -> SnapshotBody:
    try:
        snapshot = owner_of(request, db).export(run_id)
    except _EXPECTED as error:
        raise refuse(error, run_id) from None
    return SnapshotBody.model_validate(snapshot.to_dict())


@router.secured("POST", "/migration/{run_id}/fence", "handover:control", summary="Fence the run")
def fence(
    run_id: str, body: PlanIn, request: Request, db: sqlite3.Connection = Depends(get_db)
) -> OwnershipOut:
    same_run(run_id, body.run_id)
    try:
        return ownership_out(owner_of(request, db).fence(plan_of(body)))
    except _EXPECTED as error:
        raise refuse(error, run_id) from None


@router.secured("POST", "/migration/{run_id}/adopt", "handover:control", summary="Take over a run")
def adopt(
    run_id: str, body: AdoptIn, request: Request, db: sqlite3.Connection = Depends(get_db)
) -> OwnershipOut:
    same_run(run_id, body.plan.run_id)
    try:
        snapshot = Snapshot.from_dict(body.snapshot.model_dump())
    except (KeyError, ValueError, TypeError) as error:
        raise fail(422, "E-API-001", f"the snapshot is not a run: {error}", "snapshot") from None
    try:
        return ownership_out(owner_of(request, db).adopt(snapshot, plan_of(body.plan)))
    except _EXPECTED as error:
        raise refuse(error, run_id) from None


@router.secured("POST", "/migration/{run_id}/release", "handover:control", summary="Let the run go")
def release(
    run_id: str, body: PlanIn, request: Request, db: sqlite3.Connection = Depends(get_db)
) -> OwnershipOut:
    same_run(run_id, body.run_id)
    try:
        return ownership_out(owner_of(request, db).release(plan_of(body)))
    except _EXPECTED as error:
        raise refuse(error, run_id) from None
