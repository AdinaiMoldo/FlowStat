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


class ReasonOut(BaseModel):
    code: str
    step: str | None
    message: str


class MigrationRunOut(BaseModel):
    run_id: str
    owner: str  # "core" or the deployment id
    status: str
    migratable: bool
    reasons: list[ReasonOut]
    kept: list[str]
    added: list[str]
    pending: list[str]
    dropped: list[str]


class MigrationOut(BaseModel):
    workflow: str
    # `from` is a keyword in Python; the field is written `from` on the wire
    source: int = Field(serialization_alias="from")
    target: int = Field(serialization_alias="to")
    runs: list[MigrationRunOut]


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
    answering: bool | None  # did it answer the last probe (None: not running or not yet asked)
    answering_since: str | None  # since when it has given that answer


class KeyRotationOut(BaseModel):
    deployment_id: str
    key: str  # which key was replaced: control or ingest
    state: str  # rotated


class KeyRecoveryOut(BaseModel):
    deployment_id: str
    outcome: str  # nothing, completed, taken back, or away (the service did not answer)


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


# -- handover of runs between owners (api-spec section 10) -------------------------------------
class OwnershipOut(BaseModel):
    run_id: str
    epoch: int
    state: str
    peer: str | None
    version: int | None


class PlanIn(Request):
    run_id: str
    source: str
    target: str
    epoch: int
    version: int
    inputs: dict[str, Any]
    last_seq: int
    at: float


class SnapshotBody(BaseModel):
    """The run as an owner stores it, as it travels between owners."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    workflow: str
    version: int
    inputs: dict[str, Any]
    created_at: str
    events: list[dict[str, Any]]
    ownership: OwnershipOut


class AdoptIn(Request):
    snapshot: SnapshotBody
    plan: PlanIn


class NextControlKeyIn(Request):
    sha256: str  # the SHA-256 of the control key that is to be accepted beside the current one


class IngestKeyIn(Request):
    key: str  # the ingest key the service ships with from now on


class KeysOut(BaseModel):
    caller: str  # which control user made the call: "control" or "control.next"
    control_next: bool  # a second control key is accepted: a rotation is half done
    ingest_sha256: str | None  # the hash of the ingest key the service ships with


class MigrateIn(Request):
    to: int
    owner: str = "core"  # "core" or the id of a running deployment of version `to`


class Migrated(BaseModel):
    run_id: str
    source: int = Field(serialization_alias="from")
    to: int
    owner: str
    epoch: int
    status: str


class Recovered(BaseModel):
    recovered: dict[str, str]  # run id -> completed, abandoned or unreachable


class VersionStatisticsOut(BaseModel):
    runs: int
    failed: int


class WorkflowStatisticsOut(BaseModel):
    workflow: str
    runs: int
    running: int
    finished: int
    succeeded: int
    not_reached: int
    cancelled: int
    failed: int
    failure_rate: float | None  # failed / finished; null while nothing has finished
    average_duration: float | None  # seconds of the engine's clock, over finished runs
    versions: dict[str, VersionStatisticsOut]  # the version number as text -> its counts


class StatisticsOut(BaseModel):
    workflows: list[WorkflowStatisticsOut]


class FindingOut(BaseModel):
    code: str  # VAL-<group>-<number>
    severity: str  # error blocks the form; warning and info are told only
    message: str
    region: str
    file: str
    sheet: str
    row: int | None
    cell: str  # C5; empty for something that is not in the form
    indicator: str
    actual: str
    expected: str


class FormOut(BaseModel):
    region: str
    name: str
    quarter: str
    received: bool
    rows: int | None  # the rows of the form, when it was received and can be read
    size: int | None  # bytes
    problems: list[str]  # the statistical rules the form breaks, or why it cannot be read
    findings: list[FindingOut] = []  # all that the content rules found (US-54)
    version: int | None = None  # the version that the file is (US-55); null when none was kept
    versions: int = 0  # how many versions were kept for this form
    uploaded_by: str | None = None
    uploaded_at: str | None = None


class FormVersionOut(BaseModel):
    version: int
    uploaded_by: str
    uploaded_at: str
    size: int
    rows: int
    rules: str  # the set of content rules that was applied to it
    errors: int
    warnings: int
    current: bool
    removed_at: str | None
    removed_by: str | None


class FormVersionDetailOut(FormVersionOut):
    findings: list[FindingOut]  # what the rules found in this version when it was sent


class FormVersionsOut(BaseModel):
    region: str
    quarter: str
    versions: list[FormVersionOut]  # the newest first


class FormComparisonOut(BaseModel):
    region: str
    quarter: str
    older: int
    newer: int
    fixed: list[FindingOut]  # in the older version, gone in the newer
    remaining: list[FindingOut]  # in both
    introduced: list[FindingOut]  # only in the newer


class FormsOut(BaseModel):
    quarter: str
    forms: list[FormOut]


class LoginIn(Request):
    name: str
    password: str


class SessionOut(BaseModel):
    token: str  # the token of the session: send it as ``Authorization: Bearer``
    name: str
    role: str
    titles: list[str]
    expires_at: str


class MeOut(BaseModel):
    name: str
    role: str
    titles: list[str]
    permissions: list[str]  # what the role may do, for a page that shows only what works


class CampaignIn(Request):
    name: str
    year: int
    quarter: int
    regions: list[str]
    deadline: str  # a date, YYYY-MM-DD
    workflow: str
    workflow_version: int
    template: str = "regional-indicators-v1"
    rules: str = "basic"
    preliminary_min: int | None = None  # default: about three quarters of the regions
    final_min: int | None = None  # default: all of them
    approver: str = "chief_statistician"


class TransitionIn(Request):
    to: str


class RegionProgressOut(BaseModel):
    region: str
    name: str
    received: bool
    valid: bool
    problems: int
    warnings: int = 0


class ProgressOut(BaseModel):
    expected: int
    received: int
    valid: int
    invalid: int
    missing: list[str]
    regions: list[RegionProgressOut]
    preliminary_ready: bool
    final_ready: bool


class CampaignOut(BaseModel):
    id: str
    name: str
    year: int
    quarter: int
    period: str  # 2026Q3: the quarter of the forms
    regions: list[str]
    deadline: str
    workflow: str
    workflow_version: int
    template: str
    rules: str
    preliminary_min: int
    final_min: int
    approver: str
    status: str
    next: list[str]  # the changes a person may ask for now
    created_by: str
    created_at: str
    updated_at: str
    progress: ProgressOut


class ProblemOut(BaseModel):
    id: str
    region: str
    quarter: str
    code: str  # the code of the error that opened it, VAL-<group>-<number>
    indicator: str
    cell: str  # where the error was last seen
    message: str
    actual: str
    expected: str
    status: str  # OPEN, REVALIDATING, RESOLVED or REOPENED
    assignee: str | None
    opened_in: int  # the version of the form in which the error was first seen
    seen_in: int  # the latest version in which it was seen
    created_at: str
    updated_at: str


class ProblemEventOut(BaseModel):
    at: str
    by: str  # a user, or ``system`` for what a new version of the form did
    kind: str  # OPENED, ASSIGNED, COMMENTED, REVALIDATING, RESOLVED or REOPENED
    text: str
    version: int | None


class ProblemDetailOut(ProblemOut):
    events: list[ProblemEventOut]  # the history, the oldest first


class ProblemsOut(BaseModel):
    problems: list[ProblemOut]


class AssigneeIn(Request):
    assignee: str | None  # a user name, or null to take the assignment away


class CommentIn(Request):
    text: str


class ConsolidationIn(Request):
    kind: str  # PRELIMINARY or FINAL


class IncludedOut(BaseModel):
    region: str
    version: int  # the version of the form that was read
    sha256: str  # of that file


class LeftOutOut(BaseModel):
    region: str
    reason: str  # missing or invalid
    detail: str


class LinePartOut(BaseModel):
    region: str
    version: int
    value: str  # an exact decimal, as text


class LineOut(BaseModel):
    code: str
    name: str
    total: str  # an exact decimal, as text
    parts: list[LinePartOut]  # the figures the total was added from


class ConsolidationOut(BaseModel):
    campaign: str
    period: str
    number: int  # 1, 2, 3 ... for the campaign
    kind: str  # PRELIMINARY or FINAL
    complete: bool  # every region of the campaign is in it
    count: int  # the regions that are in
    expected: int
    share: str  # count of expected, in percent with one decimal
    included: list[IncludedOut]
    left_out: list[LeftOutOut]
    made_by: str
    made_at: str
    stale: bool  # a form changed or a newer result was made after it
    stale_at: str | None = None
    stale_reason: str | None = None


class ConsolidationDetailOut(ConsolidationOut):
    lines: list[LineOut]


class ConsolidationsOut(BaseModel):
    results: list[ConsolidationOut]
