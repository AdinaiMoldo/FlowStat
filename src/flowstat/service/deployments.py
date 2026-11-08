"""Deploy a published version as a service (US-30, docs/api-spec.md section 7).

This layer decides what happens and in which order; the two things that touch the operating
system, making the bundle and starting the process or container, are passed in (``Packager`` and
``Launcher``) and live in ``deploy/``. A row of the registry says what became of the request.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from flowstat.analysis.signatures import Signatures
from flowstat.compiler.plan import Plan
from flowstat.model.versions import Version, VersionStatus
from flowstat.repo.deployments import KINDS, Deployment, DeploymentStore
from flowstat.service.runs import NotPublishedError
from flowstat.service.users import hash_token
from flowstat.service.workflows import WorkflowService


class LaunchError(RuntimeError):
    """A service could not be started or stopped. ``detail`` is for the person who deploys."""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail or message


class Running:
    """Where a started service answers and how to stop it."""

    def __init__(self, endpoint: str, handle: str) -> None:
        self.endpoint = endpoint
        self.handle = handle


class Packager(Protocol):
    def __call__(
        self,
        root: Path,
        service_id: str,
        version: Version,
        api_key_sha256: str,
        title_keys_sha256: Mapping[str, str],
        now: str,
        kind: str,
    ) -> Path:
        """Write the bundle of ``version`` under ``root`` and return its directory."""
        ...


class Launcher(Protocol):
    def start(self, bundle: Path) -> Running:
        """Start the service of a bundle and return once it answers. Raises ``LaunchError``."""
        ...

    def stop(self, handle: str) -> None: ...


@dataclass(frozen=True, slots=True)
class Keys:
    """The keys of a service, shown once. ``api_key`` starts runs and reads; it holds no title.
    ``title_keys`` has one key per title that an approval of the workflow names, and each may
    answer the approvals of its own title only (DR-04)."""

    api_key: str
    title_keys: Mapping[str, str]


def approval_titles(plan: Plan, signatures: Signatures) -> tuple[str, ...]:
    """The titles named by the approvals of a plan (the literal ``role`` of a waiting step)."""
    found: set[str] = set()
    for step in plan.steps:
        signature = signatures.get(step.action)
        if signature is None or not signature.waits:
            continue
        for name, parts in step.with_:
            if name == "role" and len(parts) == 1 and isinstance(parts[0], str):
                found.add(parts[0])
    return tuple(sorted(found))


class KindUnavailableError(RuntimeError):
    """There is no launcher for this kind of deployment in this process."""


class DeploymentFailedError(RuntimeError):
    def __init__(self, deployment: Deployment) -> None:
        super().__init__(deployment.detail or "the service did not start")
        self.deployment = deployment


class NotRunningError(RuntimeError):
    pass


class DeploymentService:
    def __init__(
        self,
        db: sqlite3.Connection,
        workflows: WorkflowService,
        packager: Packager | None,
        launchers: Mapping[str, Launcher],
        root: Path,
    ) -> None:
        self.store = DeploymentStore(db)
        self.workflows = workflows
        self.packager = packager
        self.launchers = launchers
        self.root = root

    def deploy(
        self,
        workflow: str,
        number: int,
        kind: str,
        new_id: Callable[[], str],
        new_token: Callable[[], str],
        now: str,
    ) -> tuple[Deployment, Keys]:
        """Package a published version and start it. Returns the row and the keys of the service.

        The keys are made here and only their hashes are kept (in the bundle); the caller shows
        them once.
        """
        if kind not in KINDS:
            raise ValueError(f"the kind must be one of {', '.join(KINDS)}")
        version = self.workflows.get(workflow, number)
        if version.status is not VersionStatus.PUBLISHED:
            raise NotPublishedError(f"{workflow} v{number} is a draft; publish it before deploying")
        launcher = self.launchers.get(kind)
        if launcher is None or self.packager is None:
            raise KindUnavailableError(f"deployments of kind '{kind}' are not available here")
        service_id = new_id()
        titles = approval_titles(
            self.workflows.get_plan(workflow, number), self.workflows.signatures
        )
        keys = Keys(new_token(), {title: new_token() for title in titles})
        hashes = {title: hash_token(key) for title, key in keys.title_keys.items()}
        bundle = self.packager(
            self.root, service_id, version, hash_token(keys.api_key), hashes, now, kind
        )
        row = Deployment(service_id, workflow, number, kind, "starting", str(bundle), now, now)
        self.store.add(row)
        try:
            running = launcher.start(bundle)
        except LaunchError as error:
            failed = self.store.update(service_id, now, status="failed", detail=error.detail)
            raise DeploymentFailedError(failed) from error
        return (
            self.store.update(
                service_id, now, status="running", endpoint=running.endpoint, handle=running.handle
            ),
            keys,
        )

    def stop(self, deployment_id: str, now: str) -> Deployment:
        row = self.store.get(deployment_id)
        if row.status != "running" or row.handle is None:
            raise NotRunningError(f"the deployment {deployment_id} is {row.status}, not running")
        launcher = self.launchers.get(row.kind)
        if launcher is None:
            raise KindUnavailableError(f"deployments of kind '{row.kind}' are not available here")
        try:
            launcher.stop(row.handle)
        except LaunchError as error:
            self.store.update(deployment_id, now, status="running", detail=error.detail)
            raise
        return self.store.update(deployment_id, now, status="stopped")

    def get(self, deployment_id: str) -> Deployment:
        return self.store.get(deployment_id)

    def all(self) -> list[Deployment]:
        return self.store.all()
