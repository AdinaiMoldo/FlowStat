"""Move a run from its owner to another version and owner, as the core does it (US-33).

The core checks the run on the owner's own copy of its history, writes the plan in its journal and
runs the protocol of ``migration/handover.py`` against the owners: its own database and, behind
HTTP, the deployments. ``recover`` takes what a stop left in the journal forward.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from flowstat.migration.handover import Owner, OwnerUnavailableError, Plan, move, recover_all
from flowstat.migration.reasons import Reason
from flowstat.model.ownership import Ownership
from flowstat.model.versions import VersionStatus
from flowstat.repo.deployments import Deployment, DeploymentNotFoundError, DeploymentStore
from flowstat.service.handover import CoreJournal, CoreMirror, Guard, LocalOwner
from flowstat.service.migration import MigrationService, SameVersionError
from flowstat.service.remote import ServiceOwner, Transport
from flowstat.service.runs import NotPublishedError, RunNotOwnedError, RunService


@dataclass(frozen=True, slots=True)
class Moved:
    plan: Plan
    source_version: int
    ownership: Ownership  # of the new owner


class NotMigratableError(RuntimeError):
    def __init__(self, run_id: str, reasons: tuple[Reason, ...]) -> None:
        super().__init__(f"the run {run_id} cannot move: {len(reasons)} reason(s)")
        self.reasons = reasons


class TargetUnavailableError(RuntimeError):
    """The owner the run should go to is not the core and is not a running deployment of the
    version it should go to."""


class _Down:
    """An owner that cannot be called now (a deployment that stopped): every call fails."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __getattr__(self, attr: str) -> object:
        def call(*args: object) -> object:
            raise OwnerUnavailableError(f"{self.name} cannot be called")

        return call


class HandoverService:
    """What the core does to move a run: check it, then run the protocol (US-33)."""

    def __init__(
        self,
        db: sqlite3.Connection,
        runs: RunService,
        guard: Guard,
        transport_for: Callable[[Deployment], Transport] | None = None,
    ) -> None:
        self.transport_for = transport_for  # None: HTTP with the control key of the deployment
        self.db = db
        self.runs = runs
        self.guard = guard
        self.deployments = DeploymentStore(db)
        self.journal = CoreJournal(db)
        self.mirror = CoreMirror(db)

    def owners(self, names: set[str]) -> dict[str, Owner]:
        found: dict[str, Owner] = {"core": LocalOwner(self.db, self.guard)}
        for name in names - {"core"}:
            try:
                row = self.deployments.get(name)
                found[name] = (
                    ServiceOwner(self.transport_for(row))
                    if self.transport_for is not None
                    else ServiceOwner.of(row)
                )
            except (DeploymentNotFoundError, OwnerUnavailableError):
                found[name] = _Down(name)  # type: ignore[assignment]
        return found

    def migrate(self, run_id: str, version: int, owner: str, at: float) -> Moved:
        """Move the run to ``version``, driven by ``owner`` ("core" or a deployment id)."""
        run = self.runs.store.get(run_id)  # RunNotFoundError
        source = run.origin or "core"
        owners = self.owners({source, owner})
        snapshot = owners[source].export(run_id)  # the owner's copy, not the core's
        if not snapshot.ownership.owned:
            raise RunNotOwnedError(run_id, snapshot.ownership.peer, snapshot.ownership.state.value)
        if snapshot.version == version:
            raise SameVersionError(f"the run is on v{version} already")
        workflows = self.runs.workflows
        if workflows.get(run.workflow, version).status is not VersionStatus.PUBLISHED:
            raise NotPublishedError(f"{run.workflow} v{version} is a draft; publish it first")
        self._check_target(run.workflow, version, owner)
        verdict = MigrationService(self.runs).verdict(
            run.workflow, snapshot.version, version, snapshot.inputs, snapshot.events
        )
        if not verdict.migratable:
            raise NotMigratableError(run_id, verdict.reasons)
        last = snapshot.events[-1].seq if snapshot.events else -1
        plan = Plan(
            run_id, source, owner, snapshot.ownership.epoch, version, verdict.inputs, last, at
        )
        return Moved(plan, snapshot.version, move(plan, owners, self.journal, self.mirror))

    def _check_target(self, workflow: str, version: int, owner: str) -> None:
        if owner == "core":
            return
        try:
            row = self.deployments.get(owner)
        except DeploymentNotFoundError:
            raise TargetUnavailableError(f"there is no deployment {owner}") from None
        if row.state != "running":
            raise TargetUnavailableError(f"the deployment {owner} is {row.state}, not running")
        if (row.workflow, row.version) != (workflow, version):
            raise TargetUnavailableError(
                f"the deployment {owner} serves {row.workflow} v{row.version}, "
                f"not {workflow} v{version}"
            )

    def recover(self) -> dict[str, str]:
        """Take every interrupted handover forward; the outcome for each run."""
        names: set[str] = set()
        for plan in self.journal.pending():
            names |= {plan.source, plan.target}
        return recover_all(self.owners(names), self.journal, self.mirror)
