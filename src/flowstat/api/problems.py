"""The problems of the regional forms (api-spec section 13): list, read, assign, comment."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from fastapi import Depends, Query, Request
from fastapi.concurrency import run_in_threadpool

from flowstat.api.deps import config_of, get_db, require_office
from flowstat.api.errors import fail
from flowstat.api.models import (
    AssigneeIn,
    CommentIn,
    ProblemDetailOut,
    ProblemEventOut,
    ProblemOut,
    ProblemsOut,
)
from flowstat.api.secured import SecuredRouter
from flowstat.model.problems import Event, Problem, ProblemError
from flowstat.service.problems import ProblemNotFoundError, ProblemService

router = SecuredRouter(tags=["problems"])

STATUS_OF = {"E-PROB-001": 422, "E-PROB-002": 422, "E-PROB-003": 409}
FIELD_OF = {"E-PROB-001": "text", "E-PROB-002": "assignee"}


def service_of(request: Request, db: sqlite3.Connection) -> ProblemService:
    config = config_of(request)
    return ProblemService(db, config.new_id, config.clock)


def out_of(p: Problem) -> ProblemOut:
    return ProblemOut(
        id=p.id, region=p.region, quarter=p.quarter, code=p.code, indicator=p.indicator,
        cell=p.cell, message=p.message, actual=p.actual, expected=p.expected, status=p.status,
        assignee=p.assignee, opened_in=p.opened_in, seen_in=p.seen_in, created_at=p.created_at,
        updated_at=p.updated_at,
    )  # fmt: skip


def detail_of(p: Problem, events: list[Event]) -> ProblemDetailOut:
    return ProblemDetailOut(
        **out_of(p).model_dump(),
        events=[
            ProblemEventOut(at=e.at, by=e.by, kind=e.kind, text=e.text, version=e.version)
            for e in events
        ],
    )


async def work(call: Callable[[], Any]) -> Any:
    try:
        return await run_in_threadpool(call)
    except ProblemError as error:
        raise fail(
            STATUS_OF.get(error.code, 422), error.code, error.message, FIELD_OF.get(error.code)
        ) from None
    except ProblemNotFoundError:
        raise fail(404, "E-API-002", "there is no such problem") from None


@router.secured("GET", "/problems", "problems:read", summary="The problems of the forms")
async def list_problems(
    request: Request,
    quarter: str | None = Query(None, description="for example 2025Q1"),
    region: str | None = None,
    status: str | None = Query(None, description="OPEN, REVALIDATING, RESOLVED or REOPENED"),
    assignee: str | None = None,
    active: bool = Query(False, description="only the problems that still need work"),
    db: sqlite3.Connection = Depends(get_db),
) -> ProblemsOut:
    found = await work(
        lambda: service_of(request, db).find(quarter, region, status, assignee, active)
    )
    return ProblemsOut(problems=[out_of(p) for p in found])


@router.secured(
    "GET", "/problems/{problem_id}", "problems:read", summary="One problem, with its history"
)
async def get_problem(
    problem_id: str, request: Request, db: sqlite3.Connection = Depends(get_db)
) -> ProblemDetailOut:
    problem, events = await work(lambda: service_of(request, db).get(problem_id))
    return detail_of(problem, events)


@router.secured(
    "POST",
    "/problems/{problem_id}/assignee",
    "problems:write",
    summary="Give a problem to a user, or take the assignment away",
)
async def assign_problem(
    problem_id: str, body: AssigneeIn, request: Request, db: sqlite3.Connection = Depends(get_db)
) -> ProblemDetailOut:
    def change() -> tuple[Problem, list[Event]]:
        service = service_of(request, db)
        require_office(request, service.get(problem_id)[0].region)
        service.assign(problem_id, body.assignee, request.state.user.name)
        return service.get(problem_id)

    problem, events = await work(change)
    return detail_of(problem, events)


@router.secured(
    "POST",
    "/problems/{problem_id}/comments",
    "problems:write",
    status_code=201,
    summary="Add a comment to the history of a problem",
)
async def comment_problem(
    problem_id: str, body: CommentIn, request: Request, db: sqlite3.Connection = Depends(get_db)
) -> ProblemDetailOut:
    def add() -> tuple[Problem, list[Event]]:
        service = service_of(request, db)
        require_office(request, service.get(problem_id)[0].region)
        service.comment(problem_id, body.text, request.state.user.name)
        return service.get(problem_id)

    problem, events = await work(add)
    return detail_of(problem, events)
