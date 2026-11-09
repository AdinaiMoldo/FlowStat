"""Request and response bodies (Pydantic). The shapes are in docs/api-spec.md."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from flowstat.model.diagnostics import Diagnostic
from flowstat.service.completion import Completion
from flowstat.service.graph import Graph


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


class GraphNodeOut(BaseModel):
    id: str
    base: str
    action: str
    line: int
    column: int
    layer: int
    conditional: bool
    when: str | None
    goal: bool
    group: str | None
    join: str | None


class GraphEdgeOut(BaseModel):
    source: str
    target: str


class GraphGroupOut(BaseModel):
    id: str
    size: int


class GraphOut(BaseModel):
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]
    groups: list[GraphGroupOut]

    @classmethod
    def of(cls, graph: Graph) -> GraphOut:
        return cls(
            nodes=[GraphNodeOut(**asdict(n)) for n in graph.nodes],
            edges=[GraphEdgeOut(**asdict(e)) for e in graph.edges],
            groups=[GraphGroupOut(**asdict(g)) for g in graph.groups],
        )


class CompleteIn(Request):
    text: str
    line: int = Field(ge=1)
    column: int = Field(ge=1)


class SuggestionOut(BaseModel):
    label: str
    kind: str
    detail: str


class CompletionOut(BaseModel):
    items: list[SuggestionOut]
    prefix: str
    from_column: int
    note: str | None
    closed: bool

    @classmethod
    def of(cls, found: Completion) -> CompletionOut:
        return cls(
            items=[SuggestionOut(**asdict(i)) for i in found.items],
            prefix=found.prefix,
            from_column=found.from_column,
            note=found.note,
            closed=found.closed,
        )


class Previewed(BaseModel):
    diagnostics: list[DiagnosticOut]
    graph: GraphOut | None  # null when the text has errors


class VersionGraph(BaseModel):
    graph: GraphOut


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


class CoreRunSummary(RunSummary):
    origin: str | None  # null: a run of the core; else the deployment that shipped it


class DecisionIn(Request):
    step: str
    decision: str


class Decided(BaseModel):
    run_id: str
    step: str
    decision: str
    by: str


class DeployIn(Request):
    workflow: str
    version: int | None = None
    kind: str = "process"


class DeploymentOut(BaseModel):
    id: str
    workflow: str
    version: int
    kind: str
    status: str
    endpoint: str | None
    detail: str | None
    created_at: str
    updated_at: str
    runs: int  # runs this service has shipped to the core
    last_event_at: str | None  # when the core last took events from it


class DeployedOut(DeploymentOut):
    api_key: str  # shown once: only its hash is kept
    title_keys: dict[str, str]  # one per title an approval names; shown once too
    ingest_key: str  # lets the service ship its runs to this core; shown once too


class EventIn(BaseModel):
    """One event of a run, as the service stores it (model/events.py)."""

    model_config = ConfigDict(extra="forbid")

    seq: int
    kind: str
    at: float
    step: str | None = None
    state: str | None = None
    outcome: str | None = None
    code: str | None = None
    message: str | None = None
    output: dict[str, Any] | None = None
    attempt: int | None = None
    delay: float | None = None
    decision: str | None = None


class ShippedRun(BaseModel):
    run_id: str
    workflow: str
    version: int
    inputs: dict[str, Any]
    created_at: str


class IngestIn(BaseModel):
    run: ShippedRun
    events: list[EventIn]


class Ingested(BaseModel):
    run_id: str
    stored_through: int  # the number of the last event the core now has for the run


class ServiceHealth(BaseModel):
    status: str
    workflow: str
    version: int


class InputOut(BaseModel):
    name: str
    type: str
    required: bool
    default: Any = None
    pattern: str | None
    ref: str | None
    values: list[Any] | None
    minimum: float | None
    maximum: float | None


class ManifestOut(BaseModel):
    service_id: str
    text_sha256: str
    catalogue_sha256: str
    created_at: str


class SchemaOut(BaseModel):
    workflow: str
    version: int
    inputs: list[InputOut]
    manifest: ManifestOut


class ServiceStartIn(Request):
    inputs: dict[str, Any] = {}
