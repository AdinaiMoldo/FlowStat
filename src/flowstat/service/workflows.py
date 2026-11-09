"""Save, publish and read workflow versions (US-01, US-02)."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field

from flowstat.analysis import availability, guards
from flowstat.analysis.signatures import Signatures
from flowstat.analysis.types import check_types
from flowstat.compiler.catalogue import Catalogue
from flowstat.compiler.compile import CompileResult, compile_workflow
from flowstat.compiler.inputs import InputError, validate_inputs
from flowstat.compiler.plan import Plan
from flowstat.model.diagnostics import Diagnostic, Severity
from flowstat.model.versions import Version, VersionStatus
from flowstat.repo.catalogue import CatalogueRepo
from flowstat.repo.versions import VersionStore
from flowstat.service.graph import Graph, build_graph


class InvalidWorkflowError(ValueError):
    def __init__(self, diagnostics: list[Diagnostic]) -> None:
        super().__init__("; ".join(f"{d.code} {d.message}" for d in diagnostics))
        self.diagnostics = diagnostics


class PublishError(InvalidWorkflowError):
    """The draft has errors, so it cannot be published."""


@dataclass(frozen=True)
class SaveResult:
    version: Version
    diagnostics: list[Diagnostic]  # errors; empty when the draft is ready to be published
    findings: list[Diagnostic] = field(default_factory=list)  # warnings and notes (C6)


def snapshot_to_json(catalogue: Catalogue) -> str:
    return json.dumps({k: [dict(i) for i in v] for k, v in catalogue.lists.items()}, sort_keys=True)


def snapshot_from_json(text: str) -> Catalogue:
    raw = json.loads(text)
    return Catalogue({name: tuple(dict(item) for item in items) for name, items in raw.items()})


def _value_position(text: str, key: str) -> tuple[int, int]:
    """Line and column of the value of a top-level ``key:`` line, or (1, 1) when not found."""
    for number, line in enumerate(text.splitlines(), start=1):
        match = re.match(rf"{key}\s*:\s*", line)
        if match:
            return number, match.end() + 1
    return 1, 1


class WorkflowService:
    def __init__(
        self,
        db: sqlite3.Connection,
        known_actions: frozenset[str] | None = None,
        signatures: Signatures | None = None,
    ) -> None:
        self.versions_store = VersionStore(db)
        self.catalogue_repo = CatalogueRepo(db)
        self.known_actions = known_actions
        self.signatures: Signatures = signatures or {}

    # -- catalogue -----------------------------------------------------------------------------
    def set_catalogue_list(self, list_name: str, items: list[dict[str, str]]) -> None:
        self.catalogue_repo.set_list(list_name, items)

    def catalogue(self) -> Catalogue:
        lists = self.catalogue_repo.all_lists()
        return Catalogue({name: tuple(items) for name, items in lists.items()})

    # -- compilation and analysis --------------------------------------------------------------
    def _compile(self, text: str, catalogue: Catalogue) -> CompileResult:
        """The compiler, then the check of types; an error of either leaves no plan."""
        result = compile_workflow(text, catalogue, self.known_actions)
        if result.plan is None:
            return result
        errors = [
            d for d in check_types(result.plan, self.signatures) if d.severity is Severity.ERROR
        ]
        return CompileResult(None, errors) if errors else result

    def _findings(self, plan: Plan) -> list[Diagnostic]:
        """Warnings and notes: the types, then the guards."""
        found = [d for d in check_types(plan, self.signatures) if d.severity is not Severity.ERROR]
        found += guards.analyse(plan, self.signatures)
        found += availability.analyse(plan, self.signatures)
        return sorted(found, key=lambda d: (d.line, d.column, d.code))

    # -- versions ------------------------------------------------------------------------------
    def save_draft(self, name: str, text: str, now: str) -> SaveResult:
        """Store the text (a draft can hold errors) and report what is wrong with it."""
        catalogue = self.catalogue()
        version = self.versions_store.save(name, text, snapshot_to_json(catalogue), now)
        result = self._compile(text, catalogue)
        findings = self._findings(result.plan) if result.plan is not None else []
        return SaveResult(version, result.diagnostics, findings)

    def publish(self, name: str, number: int, now: str) -> Version:
        """Freeze a draft. Nothing changes when the document has errors."""
        draft = self.versions_store.get(name, number)
        catalogue = self.catalogue()
        result = self._compile(draft.text, catalogue)
        if result.plan is None:
            raise PublishError(result.diagnostics)
        problems: list[Diagnostic] = []
        if result.plan.name != name:
            line, column = _value_position(draft.text, "workflow")
            problems.append(
                Diagnostic(
                    "E-VER-002",
                    Severity.ERROR,
                    f"the workflow is called '{result.plan.name}' but is saved as '{name}'",
                    line,
                    column,
                )
            )
        if result.plan.version != number:
            line, column = _value_position(draft.text, "version")
            problems.append(
                Diagnostic(
                    "E-VER-001",
                    Severity.ERROR,
                    f"the text says version {result.plan.version} but this is version {number}",
                    line,
                    column,
                )
            )
        if problems:
            raise PublishError(sorted(problems, key=lambda d: (d.line, d.column)))
        return self.versions_store.publish(name, number, snapshot_to_json(catalogue), now)

    def check(self, text: str) -> tuple[list[Diagnostic], list[Diagnostic]]:
        """The errors and the findings of a text against the live catalogue; nothing is stored."""
        result = self._compile(text, self.catalogue())
        findings = self._findings(result.plan) if result.plan is not None else []
        return result.diagnostics, findings

    def preview(self, text: str) -> tuple[list[Diagnostic], Graph | None]:
        """The errors of a text and, when there are none, its graph; nothing is stored."""
        result = self._compile(text, self.catalogue())
        return result.diagnostics, (build_graph(result.plan) if result.plan else None)

    def graph(self, name: str, number: int) -> Graph:
        """The graph of a stored version (InvalidWorkflowError when its text does not compile)."""
        return build_graph(self.get_plan(name, number))

    def analyse(self, name: str, number: int) -> tuple[list[Diagnostic], list[Diagnostic]]:
        """The errors and the findings of a stored version, against the catalogue it reads."""
        version = self.versions_store.get(name, number)
        result = self._compile(version.text, self._catalogue_for(version))
        findings = self._findings(result.plan) if result.plan is not None else []
        return result.diagnostics, findings

    def findings(self, name: str, number: int) -> list[Diagnostic]:
        """Warnings and notes of a version: what the analysis could say about its guards."""
        version = self.versions_store.get(name, number)
        result = self._compile(version.text, self._catalogue_for(version))
        return self._findings(result.plan) if result.plan is not None else []

    def get(self, name: str, number: int) -> Version:
        return self.versions_store.get(name, number)

    def versions(self, name: str) -> list[Version]:
        return self.versions_store.versions(name)

    def _catalogue_for(self, version: Version) -> Catalogue:
        """A published version reads its own snapshot; a draft reads the live catalogue."""
        if version.status is VersionStatus.PUBLISHED:
            return snapshot_from_json(version.catalogue)
        return self.catalogue()

    def get_plan(self, name: str, number: int) -> Plan:
        version = self.versions_store.get(name, number)
        result = self._compile(version.text, self._catalogue_for(version))
        if result.plan is None:
            raise InvalidWorkflowError(result.diagnostics)
        return result.plan

    def validate_inputs(
        self, name: str, number: int, values: dict[str, object]
    ) -> tuple[dict[str, object], list[InputError]]:
        version = self.versions_store.get(name, number)
        plan = self.get_plan(name, number)
        return validate_inputs(plan, values, self._catalogue_for(version))
