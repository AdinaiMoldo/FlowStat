"""Save, publish and read workflow versions (US-01, US-02)."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass

from flowstat.compiler.catalogue import Catalogue
from flowstat.compiler.compile import compile_workflow
from flowstat.compiler.inputs import InputError, validate_inputs
from flowstat.compiler.plan import Plan
from flowstat.model.diagnostics import Diagnostic, Severity
from flowstat.model.versions import Version, VersionStatus
from flowstat.repo.catalogue import CatalogueRepo
from flowstat.repo.versions import VersionStore


class InvalidWorkflowError(ValueError):
    def __init__(self, diagnostics: list[Diagnostic]) -> None:
        super().__init__("; ".join(f"{d.code} {d.message}" for d in diagnostics))
        self.diagnostics = diagnostics


class PublishError(InvalidWorkflowError):
    """The draft has errors, so it cannot be published."""


@dataclass(frozen=True)
class SaveResult:
    version: Version
    diagnostics: list[Diagnostic]  # empty when the draft is ready to be published


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
    def __init__(self, db: sqlite3.Connection, known_actions: frozenset[str] | None = None) -> None:
        self.versions_store = VersionStore(db)
        self.catalogue_repo = CatalogueRepo(db)
        self.known_actions = known_actions

    # -- catalogue -----------------------------------------------------------------------------
    def set_catalogue_list(self, list_name: str, items: list[dict[str, str]]) -> None:
        self.catalogue_repo.set_list(list_name, items)

    def catalogue(self) -> Catalogue:
        lists = self.catalogue_repo.all_lists()
        return Catalogue({name: tuple(items) for name, items in lists.items()})

    # -- versions ------------------------------------------------------------------------------
    def save_draft(self, name: str, text: str, now: str) -> SaveResult:
        """Store the text (a draft can hold errors) and report what is wrong with it."""
        catalogue = self.catalogue()
        version = self.versions_store.save(name, text, snapshot_to_json(catalogue), now)
        result = compile_workflow(text, catalogue, self.known_actions)
        return SaveResult(version, result.diagnostics)

    def publish(self, name: str, number: int, now: str) -> Version:
        """Freeze a draft. Nothing changes when the document has errors."""
        draft = self.versions_store.get(name, number)
        catalogue = self.catalogue()
        result = compile_workflow(draft.text, catalogue, self.known_actions)
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
        result = compile_workflow(version.text, self._catalogue_for(version), self.known_actions)
        if result.plan is None:
            raise InvalidWorkflowError(result.diagnostics)
        return result.plan

    def validate_inputs(
        self, name: str, number: int, values: dict[str, object]
    ) -> tuple[dict[str, object], list[InputError]]:
        version = self.versions_store.get(name, number)
        plan = self.get_plan(name, number)
        return validate_inputs(plan, values, self._catalogue_for(version))
