"""The one error body of the API (docs/api-spec.md section 4)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("flowstat.api")


class ErrorItem(BaseModel):
    code: str
    message: str
    field: str | None = None
    line: int | None = None
    column: int | None = None
    expected: int | None = None  # a refused batch with a gap: the event number the core needs next


class ErrorBody(BaseModel):
    errors: list[ErrorItem]


class ApiError(Exception):
    def __init__(self, status: int, errors: list[ErrorItem]) -> None:
        super().__init__(errors[0].message if errors else str(status))
        self.status = status
        self.errors = errors


def fail(status: int, code: str, message: str, field: str | None = None) -> ApiError:
    return ApiError(status, [ErrorItem(code=code, message=message, field=field)])


def envelope(status: int, errors: list[ErrorItem]) -> JSONResponse:
    body = ErrorBody(errors=errors).model_dump(exclude_none=True)
    return JSONResponse(status_code=status, content=body)


# The statuses that a route may answer with an error, for the OpenAPI document.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorBody, "description": text}
    for code, text in {
        401: "no valid token (E-AUTH-001)",
        403: "the role lacks the permission (E-AUTH-002)",
        404: "not found (E-API-002)",
        409: "conflict with the state of the resource (E-API-003)",
        422: "the request is invalid (E-API-001, E-API-004, E-API-005, E-INPUT-*)",
    }.items()
}


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, error: ApiError) -> JSONResponse:
        return envelope(error.status, error.errors)

    @app.exception_handler(RequestValidationError)
    async def _shape(_: Request, error: RequestValidationError) -> JSONResponse:
        items = []
        for problem in error.errors():
            where = [str(part) for part in problem["loc"] if part not in ("body", "query", "path")]
            field = ".".join(where) or "body"
            items.append(ErrorItem(code="E-API-001", message=str(problem["msg"]), field=field))
        return envelope(422, items)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, error: StarletteHTTPException) -> JSONResponse:
        codes = {404: ("E-API-002", "not found"), 405: ("E-API-006", "method not allowed")}
        code, message = codes.get(error.status_code, ("E-API-500", str(error.detail)))
        return envelope(error.status_code, [ErrorItem(code=code, message=message)])

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, error: Exception) -> JSONResponse:
        log.exception("unhandled error", exc_info=error)  # the details stay in the log
        return envelope(500, [ErrorItem(code="E-API-500", message="internal error")])
