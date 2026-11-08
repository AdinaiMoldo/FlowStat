"""Per-request resources and the check of the caller (docs/api-spec.md sections 2 and 3)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator

from fastapi import Depends, Request

from flowstat.api.config import AppConfig
from flowstat.api.errors import ApiError, ErrorItem, fail
from flowstat.repo.db import connect
from flowstat.repo.users import User
from flowstat.service.permissions import allowed
from flowstat.service.users import UserService
from flowstat.service.workflows import WorkflowService


def config_of(request: Request) -> AppConfig:
    config: AppConfig = request.app.state.config
    return config


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    """One connection per request, closed when the response has been made."""
    db = connect(config_of(request).db_path)
    try:
        yield db
    finally:
        db.close()


def get_workflows(request: Request, db: sqlite3.Connection = Depends(get_db)) -> WorkflowService:
    registry = request.app.state.registry
    return WorkflowService(db, known_actions=registry.names(), signatures=registry.signatures())


def require(permission: str) -> Callable[..., User]:
    """A dependency: the caller must hold a valid token and a role that has ``permission``."""

    def check(request: Request, db: sqlite3.Connection = Depends(get_db)) -> User:
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        user = UserService(db).authenticate(token.strip()) if scheme.lower() == "bearer" else None
        if user is None:
            raise fail(401, "E-AUTH-001", "a valid token is required")
        if not allowed(user.role, permission):
            raise ApiError(
                403,
                [
                    ErrorItem(
                        code="E-AUTH-002",
                        message=f"the role '{user.role}' may not do this ({permission})",
                    )
                ],
            )
        request.state.user = user
        return user

    return check
