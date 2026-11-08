"""Request and response bodies (Pydantic). The shapes are in docs/api-spec.md."""

from __future__ import annotations

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
