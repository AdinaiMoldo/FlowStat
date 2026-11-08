"""A6: the registry of deployments. A row says where a published version is served and how it is."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace

STATUSES = ("starting", "running", "failed", "stopped")
KINDS = ("process", "docker")


class DeploymentNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class Deployment:
    id: str
    workflow: str
    version: int
    kind: str  # process or docker
    status: str  # starting, running, failed or stopped
    bundle: str  # the directory with the frozen copy of the version
    created_at: str
    updated_at: str
    endpoint: str | None = None  # the base URL of the service once it runs
    handle: str | None = None  # the pid of the process or the id of the container
    detail: str | None = None  # why it failed (the end of the log)


_COLUMNS = (
    "id, workflow, version, kind, status, bundle, created_at, updated_at, endpoint, handle, detail"
)


def _row(row: tuple[object, ...]) -> Deployment:
    return Deployment(*row)  # type: ignore[arg-type]


class DeploymentStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    def add(self, deployment: Deployment) -> None:
        self.db.execute(
            f"INSERT INTO deployments ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                deployment.id,
                deployment.workflow,
                deployment.version,
                deployment.kind,
                deployment.status,
                deployment.bundle,
                deployment.created_at,
                deployment.updated_at,
                deployment.endpoint,
                deployment.handle,
                deployment.detail,
            ),
        )

    def get(self, deployment_id: str) -> Deployment:
        row = self.db.execute(
            f"SELECT {_COLUMNS} FROM deployments WHERE id = ?", (deployment_id,)
        ).fetchone()
        if row is None:
            raise DeploymentNotFoundError(deployment_id)
        return _row(row)

    def all(self) -> list[Deployment]:
        rows = self.db.execute(
            f"SELECT {_COLUMNS} FROM deployments ORDER BY created_at DESC, id DESC"
        ).fetchall()
        return [_row(r) for r in rows]

    def update(
        self,
        deployment_id: str,
        now: str,
        *,
        status: str,
        endpoint: str | None = None,
        handle: str | None = None,
        detail: str | None = None,
    ) -> Deployment:
        """Change the state of a row; the endpoint, handle and detail are kept unless given."""
        current = self.get(deployment_id)
        changed = replace(
            current,
            status=status,
            updated_at=now,
            endpoint=endpoint if endpoint is not None else current.endpoint,
            handle=handle if handle is not None else current.handle,
            detail=detail if detail is not None else current.detail,
        )
        self.db.execute(
            "UPDATE deployments SET status = ?, endpoint = ?, handle = ?, detail = ?, "
            "updated_at = ? WHERE id = ?",
            (
                changed.status,
                changed.endpoint,
                changed.handle,
                changed.detail,
                now,
                deployment_id,
            ),
        )
        return changed
