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
    status: str  # starting, running, failed or stopped (see ``state`` for what is shown)
    bundle: str  # the directory with the frozen copy of the version
    created_at: str
    updated_at: str
    endpoint: str | None = None  # the base URL of the service once it runs
    handle: str | None = None  # the pid of the process or the id of the container
    detail: str | None = None  # why it failed (the end of the log)
    ingest_key_sha256: str | None = None  # the key of the service for shipping its events
    last_event_at: str | None = None  # when the core last stored events from this service
    # The fourth key, which lets the core move runs in and out of the service (US-33). The core
    # has to present it, so it is kept as it is, here and nowhere else; it is never listed.
    control_key: str | None = None
    # Whether the service answered its last probe (US-37): None when it was never asked or is not
    # running, and since when it has given that answer. The status above is only what the core
    # did to the process; this is what the process is seen to do.
    answering: bool | None = None
    answering_since: str | None = None

    @property
    def state(self) -> str:
        """What the registry shows: ``unreachable`` is a running service that does not answer."""
        return (
            "unreachable" if self.status == "running" and self.answering is False else self.status
        )


_COLUMNS = (
    "id, workflow, version, kind, status, bundle, created_at, updated_at, endpoint, handle, "
    "detail, ingest_key_sha256, last_event_at, control_key, answering, answering_since"
)


def _row(row: tuple[object, ...]) -> Deployment:
    *rest, answering, since = row
    flag = None if answering is None else bool(answering)
    return Deployment(*rest, flag, since)  # type: ignore[arg-type,call-arg]


class DeploymentStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    def add(self, deployment: Deployment) -> None:
        self.db.execute(
            f"INSERT INTO deployments ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                deployment.ingest_key_sha256,
                deployment.last_event_at,
                deployment.control_key,
                None if deployment.answering is None else int(deployment.answering),
                deployment.answering_since,
            ),
        )

    def get(self, deployment_id: str) -> Deployment:
        row = self.db.execute(
            f"SELECT {_COLUMNS} FROM deployments WHERE id = ?", (deployment_id,)
        ).fetchone()
        if row is None:
            raise DeploymentNotFoundError(deployment_id)
        return _row(row)

    def by_ingest_hash(self, key_sha256: str) -> Deployment | None:
        """The deployment whose ingest key has this hash, or None."""
        if not key_sha256:
            return None
        row = self.db.execute(
            f"SELECT {_COLUMNS} FROM deployments WHERE ingest_key_sha256 = ?", (key_sha256,)
        ).fetchone()
        return _row(row) if row is not None else None

    def touch(self, deployment_id: str, at: str) -> None:
        """Note when events of this service were last stored."""
        self.get(deployment_id)
        self.db.execute(
            "UPDATE deployments SET last_event_at = ? WHERE id = ?", (at, deployment_id)
        )

    def mark_answering(
        self, deployment_id: str, answering: bool | None, at: str | None, detail: str | None = None
    ) -> Deployment:
        """Record what a probe saw, and since when. ``detail`` replaces the reason when given
        (an empty text clears it); the time of the row moves, because the row changed."""
        current = self.get(deployment_id)
        shown = current.detail if detail is None else detail
        self.db.execute(
            "UPDATE deployments SET answering = ?, answering_since = ?, detail = ?, "
            "updated_at = ? WHERE id = ?",
            (
                None if answering is None else int(answering),
                at,
                shown,
                at or current.updated_at,
                deployment_id,
            ),
        )
        return self.get(deployment_id)

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
        if status != "running":  # only a running service is asked, so nothing is known otherwise
            changed = replace(changed, answering=None, answering_since=None)
        self.db.execute(
            "UPDATE deployments SET status = ?, endpoint = ?, handle = ?, detail = ?, "
            "updated_at = ?, answering = ?, answering_since = ? WHERE id = ?",
            (
                changed.status,
                changed.endpoint,
                changed.handle,
                changed.detail,
                now,
                None if changed.answering is None else int(changed.answering),
                changed.answering_since,
                deployment_id,
            ),
        )
        return changed
