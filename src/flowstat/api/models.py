"""Request and response bodies (Pydantic). The shapes are in docs/api-spec.md."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from flowstat.model.diagnostics import Diagnostic


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiagnosticOut(BaseModel):
    code: str
    severity: str
    message: str
    line: int
    column: int

    @classmethod
    def of(cls, d: Diagnostic) -> DiagnosticOut:
        return cls(
            code=d.code, severity=d.severity.value, message=d.message, line=d.line, column=d.column
        )


class TextIn(Request):
    text: str


class Checked(BaseModel):
    diagnostics: list[DiagnosticOut]  # errors
    findings: list[DiagnosticOut]  # warnings and notes


class DraftSaved(Checked):
    workflow: str
    version: int
    status: str


class Published(BaseModel):
    workflow: str
    version: int
    status: str
    published_at: str | None
    findings: list[DiagnosticOut]


class WorkflowSummary(BaseModel):
    name: str
    latest: int
    latest_published: int | None


class VersionSummary(BaseModel):
    number: int
    status: str
    created_at: str
    published_at: str | None


class VersionDetail(Checked):
    workflow: str
    version: int
    status: str
    text: str
    created_at: str
    published_at: str | None


class CatalogueIn(Request):
    items: list[dict[str, str]]


class CatalogueSaved(BaseModel):
    list: str
    count: int


class StartIn(Request):
    inputs: dict[str, Any] = {}
    version: int | None = None


class Started(BaseModel):
    run_id: str
    workflow: str
    version: int
    status: str


class StepOut(BaseModel):
    step: str
    state: str
    started_at: float | None
    finished_at: float | None
    code: str | None
    message: str | None
    output: dict[str, Any] | None
    attempts: int
    compensated_at: float | None


class RunOut(BaseModel):
    run_id: str
    workflow: str
    version: int
    status: str
    outcome: str | None
    started_at: float | None
    finished_at: float | None
    steps: list[StepOut]


class RunSummary(BaseModel):
    run_id: str
    workflow: str
    version: int
    status: str
    outcome: str | None
    created_at: str


class DecisionIn(Request):
    step: str
    decision: str


class Decided(BaseModel):
    run_id: str
    step: str
    decision: str
    by: str
