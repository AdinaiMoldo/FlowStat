"""The routes by which the core replaces the control key and the ingest key of a deployed service
(US-40, api-spec section 7). Only the control key may call them (``handover:control``)."""

from __future__ import annotations

import sqlite3

from fastapi import Depends, Request

from flowstat.api.deps import get_db
from flowstat.api.errors import fail
from flowstat.api.models import IngestKeyIn, KeysOut, NextControlKeyIn
from flowstat.api.secured import SecuredRouter
from flowstat.repo.users import UserExistsError
from flowstat.service.keys import KeysError, KeyState, ServiceKeys

router = SecuredRouter(tags=["keys"])


def keys_of(request: Request, db: sqlite3.Connection) -> ServiceKeys:
    return ServiceKeys(db, getattr(request.app.state, "env_ingest_key", None))


def out(state: KeyState) -> KeysOut:
    return KeysOut(
        caller=state.caller, control_next=state.control_next, ingest_sha256=state.ingest_sha256
    )


@router.secured("GET", "/keys", "handover:control", summary="Which keys this service accepts")
def read_keys(request: Request, db: sqlite3.Connection = Depends(get_db)) -> KeysOut:
    return out(keys_of(request, db).state(request.state.user.name))


@router.secured(
    "POST", "/keys/control/prepare", "handover:control", summary="Accept a second control key"
)
def prepare(
    body: NextControlKeyIn, request: Request, db: sqlite3.Connection = Depends(get_db)
) -> KeysOut:
    keys = keys_of(request, db)
    try:
        keys.prepare(request.state.user.name, body.sha256, request.app.state.config.clock())
    except (KeysError, UserExistsError) as error:
        raise fail(409, "E-API-003", str(error)) from None
    return out(keys.state(request.state.user.name))


@router.secured(
    "POST", "/keys/control/settle", "handover:control", summary="Keep the key used for this call"
)
def settle(request: Request, db: sqlite3.Connection = Depends(get_db)) -> KeysOut:
    keys = keys_of(request, db)
    try:
        keys.settle(request.state.user.name)
    except KeysError as error:
        raise fail(409, "E-API-003", str(error)) from None
    return out(keys.state("control"))


@router.secured("POST", "/keys/ingest", "handover:control", summary="Use this ingest key")
def store_ingest(
    body: IngestKeyIn, request: Request, db: sqlite3.Connection = Depends(get_db)
) -> KeysOut:
    keys = keys_of(request, db)
    try:
        keys.store_ingest(body.key)
    except KeysError as error:
        raise fail(422, "E-API-001", str(error), "key") from None
    return out(keys.state(request.state.user.name))
