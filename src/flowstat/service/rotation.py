"""Replace the control key or the ingest key of a deployed service (US-40, api-spec section 7).

A rotation is a short protocol with a record in the registry, so a stop at any point is finished
or taken back when the core looks at it again (``recover``):

control key (the core presents it)
    1. the new key is written to the row (``control_key_next``);
    2. the service is told its hash with the old key (``prepare``): it accepts both;
    3. the service is told with the new key to keep it alone (``settle``): the old one is refused;
    4. the row keeps the new key.
ingest key (the service presents it; the core holds its hash)
    1. the hash of the new key is written to the row: the core accepts both;
    2. the service is given the key with the control key and ships with it from its next send;
    3. the row keeps the new hash: the old key is refused.

After a stop the service is asked. If it knows the new key the rotation is carried to its end; if
it does not, it is taken back, and the old key is still the only one. Both ends leave exactly one
key. In between, the two work, which is what lets a send that is under way with the old key finish.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from flowstat.migration.handover import OwnerUnavailableError
from flowstat.repo.deployments import Deployment, DeploymentStore
from flowstat.service.remote import Transport, http_transport
from flowstat.service.users import MIN_TOKEN_LENGTH, hash_token

KINDS = ("control", "ingest")
KeyTransport = Callable[[Deployment, str], Transport]  # (the row, the key to present)


class RotationRefusedError(RuntimeError):
    """The rotation cannot be done now (the service is not running, or it refused)."""


class ServiceAwayError(RuntimeError):
    """The service did not answer. The record stays: ``recover`` finishes or takes it back."""


def _http(row: Deployment, key: str) -> Transport:
    return http_transport(row.endpoint or "", key)


class KeyRotation:
    def __init__(self, db: sqlite3.Connection, transport_for: KeyTransport | None = None) -> None:
        self.store = DeploymentStore(db)
        self.transport_for = transport_for or _http

    # -- the call ------------------------------------------------------------------------
    def _call(
        self, row: Deployment, key: str, method: str, path: str, body: dict[str, object] | None
    ) -> tuple[int, dict[str, object]]:
        try:
            return self.transport_for(row, key)(method, path, body)
        except OwnerUnavailableError as error:
            raise ServiceAwayError(str(error)) from None

    # -- rotate ----------------------------------------------------------------------------
    def rotate(self, deployment_id: str, kind: str, new_key: str, now: str) -> Deployment:
        if kind not in KINDS:
            raise ValueError(f"the key to rotate is one of {', '.join(KINDS)}")
        if len(new_key) < MIN_TOKEN_LENGTH:
            raise ValueError(f"a key has at least {MIN_TOKEN_LENGTH} characters")
        row = self.store.get(deployment_id)
        if row.state != "running" or not row.endpoint or not row.control_key:
            raise RotationRefusedError(
                f"the deployment {deployment_id} is {row.state}; only a service that answers "
                "can have a key replaced"
            )
        if row.control_key_next or row.ingest_key_next_sha256:
            self.recover(deployment_id, now)  # an earlier rotation comes first
            row = self.store.get(deployment_id)
            if row.control_key_next or row.ingest_key_next_sha256:
                raise RotationRefusedError("an earlier rotation is not finished yet")
        if kind == "control":
            return self._control(row, new_key, now)
        return self._ingest(row, new_key, now)

    def _control(self, row: Deployment, new_key: str, now: str) -> Deployment:
        assert row.control_key is not None
        self.store.begin_control(row.id, new_key)
        status, body = self._call(
            row, row.control_key, "POST", "/keys/control/prepare", {"sha256": hash_token(new_key)}
        )
        if status != 200:
            self.store.end_control(row.id, now, keep_next=False)  # nothing reached the service
            raise RotationRefusedError(_why(status, body))
        status, body = self._call(row, new_key, "POST", "/keys/control/settle", None)
        if status != 200:
            raise ServiceAwayError(_why(status, body))  # recover decides
        return self.store.end_control(row.id, now, keep_next=True)

    def _ingest(self, row: Deployment, new_key: str, now: str) -> Deployment:
        assert row.control_key is not None
        self.store.begin_ingest(row.id, hash_token(new_key))
        status, body = self._call(row, row.control_key, "POST", "/keys/ingest", {"key": new_key})
        if status != 200:
            if status in (401, 403, 409, 422):  # refused: the service did not take the key
                self.store.end_ingest(row.id, now, keep_next=False)
                raise RotationRefusedError(_why(status, body))
            raise ServiceAwayError(_why(status, body))
        return self.store.end_ingest(row.id, now, keep_next=True)

    # -- recover ---------------------------------------------------------------------------
    def recover(self, deployment_id: str, now: str) -> str:
        """Finish or take back a rotation that did not end. Returns what was done:
        ``nothing``, ``completed``, ``taken back`` or ``away`` (the service did not answer)."""
        row = self.store.get(deployment_id)
        done: list[str] = []
        try:
            if row.control_key_next:
                done.append(self._recover_control(row, now))
            row = self.store.get(deployment_id)
            if row.ingest_key_next_sha256:
                done.append(self._recover_ingest(row, now))
        except ServiceAwayError:
            return "away"
        if not done:
            return "nothing"
        return "completed" if "completed" in done else "taken back"

    def _recover_control(self, row: Deployment, now: str) -> str:
        assert row.control_key_next is not None
        # The new key is known to the service only if it was prepared: ask with it.
        status, _ = self._call(row, row.control_key_next, "GET", "/keys", None)
        if status in (401, 403):
            self.store.end_control(row.id, now, keep_next=False)
            return "taken back"
        if status != 200:
            raise ServiceAwayError(f"the service answered {status}")
        status, _ = self._call(row, row.control_key_next, "POST", "/keys/control/settle", None)
        if status != 200:
            raise ServiceAwayError(f"the service answered {status}")
        self.store.end_control(row.id, now, keep_next=True)
        return "completed"

    def _recover_ingest(self, row: Deployment, now: str) -> str:
        assert row.control_key is not None and row.ingest_key_next_sha256 is not None
        status, body = self._call(row, row.control_key, "GET", "/keys", None)
        if status != 200:
            raise ServiceAwayError(_why(status, body))
        if body.get("ingest_sha256") == row.ingest_key_next_sha256:
            self.store.end_ingest(row.id, now, keep_next=True)
            return "completed"
        self.store.end_ingest(row.id, now, keep_next=False)
        return "taken back"

    def recover_all(self, now: str) -> dict[str, str]:
        """Every deployment that has a rotation under way, at the start of the core."""
        return {
            row.id: self.recover(row.id, now)
            for row in self.store.all()
            if row.control_key_next or row.ingest_key_next_sha256
        }


def _why(status: int, body: dict[str, object]) -> str:
    errors = body.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return f"the service answered {status}: {errors[0].get('message', '')}"
    return f"the service answered {status}"
