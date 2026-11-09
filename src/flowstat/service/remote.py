"""A deployed service as an owner of runs: the calls of the handover over HTTP (US-33).

The core presents the control key of the deployment. An answer that says the call cannot be made
in the state the run is in (409) or that the run is not there (404) becomes the error that the
local owner would have raised; no answer at all, or a failure of the server, is
``OwnerUnavailableError``: the handover is taken up again later.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping

from flowstat.migration.handover import OwnerUnavailableError, Plan, Snapshot
from flowstat.model.ownership import Ownership, OwnerState
from flowstat.repo.deployments import Deployment
from flowstat.repo.ownership import NotOwnerError, RunChangedError
from flowstat.repo.runs import EventConflictError, RunNotFoundError
from flowstat.service.handover import RunBusyError

Transport = Callable[[str, str, dict[str, object] | None], tuple[int, dict[str, object]]]


def http_transport(base_url: str, key: str, timeout: float = 10.0) -> Transport:
    """Calls against ``base_url`` with the control key. Raises ``OwnerUnavailableError`` when
    there is no answer."""

    def call(
        method: str, path: str, body: dict[str, object] | None
    ) -> tuple[int, dict[str, object]]:
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}{path}",
            data=json.dumps(body).encode("utf8") if body is not None else None,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, _json(response.read())
        except urllib.error.HTTPError as error:
            return error.code, _json(error.read())
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise OwnerUnavailableError(str(error)) from None

    return call


def _json(raw: bytes) -> dict[str, object]:
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _message(body: Mapping[str, object]) -> tuple[str, str]:
    errors = body.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return str(errors[0].get("code", "")), str(errors[0].get("message", ""))
    return "", ""


def _ownership(data: Mapping[str, object]) -> Ownership:
    version = data.get("version")
    peer = data.get("peer")
    return Ownership(
        str(data["run_id"]),
        int(data["epoch"]),  # type: ignore[call-overload]
        OwnerState(str(data["state"])),
        str(peer) if peer is not None else None,
        int(version) if version is not None else None,  # type: ignore[call-overload]
    )


class ServiceOwner:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    @classmethod
    def of(cls, deployment: Deployment) -> ServiceOwner:
        """The owner behind a running deployment; it needs its endpoint and its control key."""
        if deployment.status != "running" or not deployment.endpoint or not deployment.control_key:
            raise OwnerUnavailableError(f"the deployment {deployment.id} cannot be called")
        return cls(http_transport(deployment.endpoint, deployment.control_key))

    def _ask(
        self, method: str, run_id: str, action: str, body: dict[str, object] | None = None
    ) -> dict[str, object]:
        status, answer = self.transport(method, f"/migration/{run_id}/{action}", body)
        if status == 200:
            return answer
        code, message = _message(answer)
        if status == 404:
            raise RunNotFoundError(run_id)
        if status == 409 and code == "E-MIG-010":
            raise NotOwnerError(Ownership(run_id, -1, OwnerState.MIGRATED_OUT, None)) from None
        if status == 409 and "changed" in message:
            raise RunChangedError(run_id, -1, -1)
        if status == 409 and "driven" in message:
            raise RunBusyError(message)
        if status == 409:
            raise EventConflictError(message)
        text = f"{status} {code} {message}".strip()
        if status >= 500:
            raise OwnerUnavailableError(text)
        raise RuntimeError(f"the service refused the call: {text}")

    def ownership(self, run_id: str) -> Ownership:
        return _ownership(self._ask("GET", run_id, "ownership"))

    def export(self, run_id: str) -> Snapshot:
        return Snapshot.from_dict(self._ask("GET", run_id, "export"))

    def fence(self, plan: Plan) -> Ownership:
        return _ownership(self._ask("POST", plan.run_id, "fence", _plan(plan)))

    def adopt(self, snapshot: Snapshot, plan: Plan) -> Ownership:
        body: dict[str, object] = {"snapshot": snapshot.to_dict(), "plan": _plan(plan)}
        return _ownership(self._ask("POST", plan.run_id, "adopt", body))

    def release(self, plan: Plan) -> Ownership:
        return _ownership(self._ask("POST", plan.run_id, "release", _plan(plan)))

    def retarget(self, plan: Plan) -> Ownership:
        raise NotImplementedError("a service does not move a run to itself through the core")


def _plan(plan: Plan) -> dict[str, object]:
    body: dict[str, object] = json.loads(plan.to_json())
    return body
