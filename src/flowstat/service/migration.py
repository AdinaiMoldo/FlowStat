"""Which runs of a workflow can move from one version to another (US-32).

The check reads the stored history of each unfinished run and the compiled plans of the two
versions, each with the catalogue it froze; it stores nothing and changes no run.
"""

from __future__ import annotations

from dataclasses import dataclass

from flowstat.migration.conformance import Verdict, check
from flowstat.model.versions import VersionStatus
from flowstat.repo.runs import StoredRun
from flowstat.repo.versions import VersionNotFoundError
from flowstat.service.runs import NotPublishedError, RunService


class SameVersionError(ValueError):
    """A run cannot move to the version it is already on."""


@dataclass(frozen=True, slots=True)
class RunCheck:
    run_id: str
    owner: str  # "core" or the deployment that shipped the run
    status: str
    verdict: Verdict


class MigrationService:
    def __init__(self, runs: RunService) -> None:
        self.runs = runs

    def report(self, name: str, source: int, target: int) -> list[RunCheck]:
        """The verdict for every unfinished run of ``name`` at version ``source``, oldest first.

        Both versions must exist and be published (NotPublishedError, VersionNotFoundError) and
        differ (SameVersionError).
        """
        if source == target:
            raise SameVersionError("the two versions are the same")
        workflows = self.runs.workflows
        for number in (source, target):
            try:
                version = workflows.get(name, number)
            except VersionNotFoundError:
                raise VersionNotFoundError(f"{name} v{number}") from None
            if version.status is not VersionStatus.PUBLISHED:
                raise NotPublishedError(f"{name} v{number} is a draft; publish it first")
        found = []
        stored = sorted(self.runs.list_runs(name), key=lambda r: (r.created_at, r.run_id))
        for run in stored:
            if run.version != source:
                continue
            status, _ = self.runs.status(run.run_id)
            if status != "FINISHED":
                found.append(self.check_run(run, target, status))
        return found

    def check_run(self, run: StoredRun, target: int, status: str | None = None) -> RunCheck:
        """The verdict for one stored run if it moved to version ``target`` of its workflow."""
        workflows = self.runs.workflows
        events = self.runs.store.events(run.run_id)
        verdict = check(
            workflows.get_plan(run.workflow, run.version),
            workflows.get_plan(run.workflow, target),
            events,
            run.inputs,
            workflows.signatures,
            workflows.catalogue_of(run.workflow, target),
        )
        shown = status if status is not None else self.runs.status(run.run_id)[0]
        return RunCheck(run.run_id, run.origin or "core", shown, verdict)
