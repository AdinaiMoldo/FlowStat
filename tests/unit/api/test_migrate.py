"""Moving a run to another version and owner through the routes (US-33).

The core is a real app over a database; the services are real apps over their own databases, built
from bundles the packager made, and the core reaches them through their routes in-process.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient

from flowstat.api.config import AppConfig
from flowstat.api.service import create_service_app
from flowstat.deploy.package import build_bundle
from flowstat.migration.handover import OwnerUnavailableError
from flowstat.model.events import EventKind
from flowstat.repo.db import connect
from flowstat.repo.deployments import Deployment, DeploymentStore
from flowstat.repo.runs import RunStore
from flowstat.service.bundle import read_bundle
from flowstat.service.users import hash_token
from flowstat.service.workflows import WorkflowService
from tests.unit.actions.helpers import write_all_forms
from tests.unit.api.helpers import EXAMPLE, NAME, Env, T, make_env

CONTROL = "c0" * 32
API_KEY = "5e" * 32
TITLE_KEY = "7a" * 32
RETRY = EXAMPLE.replace("retry: {max: 3,", "retry: {max: 5,")
CHANGED = EXAMPLE.replace(
    '    with: {parts: "${check[*].rows}"}\n',
    '    with: {parts: "${check[*].rows}"}\n    ensures: "len(${consolidate.rows}) > 0"\n',
)
QUARTER = {"inputs": {"quarter": "2025Q1"}}
Transport = Callable[[str, str, dict[str, object] | None], tuple[int, dict[str, object]]]


@dataclass
class Service:
    client: TestClient
    bundle: Path
    key: str = API_KEY

    def call(self, key: str, method: str, path: str, json: object = None) -> httpx2.Response:
        return self.client.request(
            method, path, headers={"Authorization": f"Bearer {key}"}, json=json
        )

    def events(self, run_id: str) -> list[Any]:
        db = connect(str(self.bundle / "service.db"))
        try:
            return RunStore(db).events(run_id)
        finally:
            db.close()


@dataclass
class World:
    core: Env
    services: dict[str, Service] = field(default_factory=dict)
    down: set[str] = field(default_factory=set)

    def transport(self, deployment: Deployment) -> Transport:
        service = self.services[deployment.id]

        def call(
            method: str, path: str, body: dict[str, object] | None
        ) -> tuple[int, dict[str, object]]:
            if deployment.id in self.down:
                raise OwnerUnavailableError(f"{deployment.id} does not answer")
            answer = service.call(CONTROL, method, path, body)
            return answer.status_code, answer.json()

        return call


def publish(env: Env, text: str) -> int:
    listing = env.call("designer", "GET", f"/workflows/{NAME}/versions")
    number = len(listing.json()) + 1 if listing.status_code == 200 else 1
    text = text.replace("version: 1", f"version: {number}", 1)
    assert (
        env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": text}).status_code
        == 201
    )
    assert (
        env.call("designer", "POST", f"/workflows/{NAME}/versions/{number}/publish").status_code
        == 200
    )
    return number


def deploy_service(w: World, tmp_path: Path, deployment_id: str, number: int) -> Service:
    """A service of version ``number``, registered at the core as running."""
    db = connect(w.core.config.db_path)
    version = WorkflowService(db).get(NAME, number)
    hashes = {"chief_statistician": hash_token(TITLE_KEY)}
    bundle = build_bundle(
        tmp_path / "bundles", deployment_id, version, hash_token(API_KEY), hashes, T, "process",
        control_key_sha256=hash_token(CONTROL),
    )  # fmt: skip
    DeploymentStore(db).add(
        Deployment(
            deployment_id, NAME, number, "process", "running", str(bundle), T, T,
            endpoint=f"http://127.0.0.1:9{number}", control_key=CONTROL,
        )
    )  # fmt: skip
    db.close()
    write_all_forms(bundle / "data", "2025Q1")
    ids = (f"{deployment_id}-r{n}" for n in itertools.count(1))
    ticks = itertools.count(1)
    config = AppConfig(
        db_path=str(bundle / "service.db"), data_dir=bundle / "data",
        target=str(bundle / "target.db"),
        clock=lambda: T, time=lambda: float(next(ticks)), sleep=lambda seconds: None,
        new_id=lambda: next(ids), new_token=lambda: "unused", submit=lambda job: job(),
        bundles=bundle, workers=2,
    )  # fmt: skip
    service = Service(TestClient(create_service_app(config, read_bundle(bundle))), bundle)
    w.services[deployment_id] = service
    return service


@pytest.fixture
def w(tmp_path: Path) -> World:
    env = make_env(tmp_path)
    world = World(env)
    env.client.app.state.transport_for = world.transport  # type: ignore[attr-defined]
    write_all_forms(env.config.data_dir, "2025Q1")
    assert publish(env, EXAMPLE) == 1
    assert publish(env, RETRY) == 2  # may follow v1
    assert publish(env, CHANGED) == 3  # may not
    assert publish(env, RETRY) == 4  # the same as v2
    return world


def start_waiting(w: World) -> str:
    started = w.core.call(
        "operator", "POST", f"/workflows/{NAME}/runs", json=QUARTER | {"version": 1}
    )
    assert started.status_code == 202, started.text
    run_id: str = started.json()["run_id"]
    assert w.core.call("operator", "GET", f"/runs/{run_id}").json()["status"] == "WAITING"
    return run_id


def migrate(
    w: World, run_id: str, to: int, owner: str = "core", role: str = "operator"
) -> httpx2.Response:
    return w.core.call(role, "POST", f"/runs/{run_id}/migrate", json={"to": to, "owner": owner})


def approve(w: World, run_id: str) -> httpx2.Response:
    return w.core.call(
        "operator",
        "POST",
        f"/runs/{run_id}/decisions",
        json={"step": "approve", "decision": "approved"},
    )


def kinds(events: list[Any]) -> list[EventKind]:
    return [e.kind for e in events]


# -- inside the core -----------------------------------------------------------------------------
def test_a_waiting_run_moves_to_the_next_version_in_the_core_and_finishes_under_it(
    w: World,
) -> None:
    run_id = start_waiting(w)
    answer = migrate(w, run_id, 2)
    assert answer.status_code == 200, answer.text
    assert answer.json() == {
        "run_id": run_id, "from": 1, "to": 2, "owner": "core", "epoch": 1, "status": "WAITING",
    }  # fmt: skip
    listing = w.core.call("operator", "GET", "/runs").json()
    assert [(r["run_id"], r["version"]) for r in listing] == [(run_id, 2)]
    assert approve(w, run_id).status_code == 202
    report = w.core.call("operator", "GET", f"/runs/{run_id}").json()
    assert (report["version"], report["status"], report["outcome"]) == (2, "FINISHED", "SUCCEEDED")
    db = connect(w.core.config.db_path)
    assert kinds(RunStore(db).events(run_id)).count(EventKind.RUN_MIGRATED) == 1
    db.close()


def test_a_run_whose_history_the_new_version_cannot_explain_stays_where_it_is(w: World) -> None:
    run_id = start_waiting(w)
    answer = migrate(w, run_id, 3)
    assert answer.status_code == 409
    items = answer.json()["errors"]
    assert items[0]["code"] == "E-API-003"
    assert [(i["code"], i.get("field")) for i in items[1:]] == [("E-MIG-004", "consolidate")]
    assert w.core.call("operator", "GET", "/runs").json()[0]["version"] == 1
    assert approve(w, run_id).status_code == 202  # still driven, still under v1


def test_the_answers_for_a_wrong_request(w: World) -> None:
    run_id = start_waiting(w)
    assert migrate(w, "nope", 2).status_code == 404
    assert migrate(w, run_id, 1).status_code == 422  # it is on v1
    assert migrate(w, run_id, 9).status_code == 404
    assert migrate(w, run_id, 2, owner="svc-9").status_code == 409  # no such deployment
    assert migrate(w, run_id, 2, role="designer").status_code == 403
    assert (
        w.core.call("operator", "POST", f"/runs/{run_id}/migrate", json={"to": "x"}).status_code
        == 422
    )


def test_a_finished_run_is_never_moved(w: World) -> None:
    run_id = start_waiting(w)
    approve(w, run_id)
    answer = migrate(w, run_id, 2)
    assert answer.status_code == 409
    assert answer.json()["errors"][1]["code"] == "E-MIG-001"


def test_a_target_deployment_must_serve_the_version_asked(w: World, tmp_path: Path) -> None:
    run_id = start_waiting(w)
    deploy_service(w, tmp_path, "svc-2", 2)
    answer = migrate(w, run_id, 4, owner="svc-2")  # svc-2 serves v2, not v4
    assert answer.status_code == 409 and "serves" in answer.json()["errors"][0]["message"]
    stopped = connect(w.core.config.db_path)
    stopped.execute("UPDATE deployments SET status = 'stopped' WHERE id = 'svc-2'")
    stopped.close()
    assert migrate(w, run_id, 2, owner="svc-2").status_code == 409


# -- between the core and a service ----------------------------------------------------------------
def test_a_run_moves_into_a_service_which_then_drives_it_and_the_core_refuses_its_commands(
    w: World, tmp_path: Path
) -> None:
    run_id = start_waiting(w)
    service = deploy_service(w, tmp_path, "svc-2", 2)
    answer = migrate(w, run_id, 2, owner="svc-2")
    assert answer.status_code == 200, answer.text
    assert answer.json()["epoch"] == 1 and answer.json()["owner"] == "svc-2"
    # the core knows where it went
    [row] = w.core.call("operator", "GET", "/runs?deployment=svc-2").json()
    assert (row["run_id"], row["origin"], row["version"]) == (run_id, "svc-2", 2)
    refused = approve(w, run_id)
    assert refused.status_code == 409
    assert (
        refused.json()["errors"][0]["code"] == "E-MIG-010"
        and "svc-2" in refused.json()["errors"][0]["message"]
    )
    # the service has it, and finishes it
    assert service.call(API_KEY, "GET", f"/runs/{run_id}").json()["status"] == "WAITING"
    decided = service.call(
        TITLE_KEY, "POST", f"/runs/{run_id}/decisions", {"step": "approve", "decision": "approved"}
    )
    assert decided.status_code == 202, decided.text
    report = service.call(API_KEY, "GET", f"/runs/{run_id}").json()
    assert (report["status"], report["outcome"]) == ("FINISHED", "SUCCEEDED")
    assert kinds(service.events(run_id)).count(EventKind.RUN_MIGRATED) == 1
    # the core's own driver refuses a resume of the run it no longer drives
    own = w.core.call(
        "operator",
        "POST",
        f"/runs/{run_id}/decisions",
        json={"step": "approve", "decision": "approved"},
    )
    assert own.json()["errors"][0]["code"] == "E-MIG-010"


def test_a_run_moves_out_of_a_service_into_the_core(w: World, tmp_path: Path) -> None:
    run_id = start_waiting(w)
    service = deploy_service(w, tmp_path, "svc-2", 2)
    assert migrate(w, run_id, 2, owner="svc-2").status_code == 200
    back = migrate(w, run_id, 4, owner="core")  # v4 is the same as v2
    assert back.status_code == 200, back.text
    assert back.json()["epoch"] == 2 and back.json()["from"] == 2
    [row] = w.core.call("operator", "GET", "/runs").json()
    assert (row["origin"], row["version"]) == (None, 4)
    refused = service.call(
        TITLE_KEY, "POST", f"/runs/{run_id}/decisions", {"step": "approve", "decision": "approved"}
    )
    assert refused.status_code == 409 and refused.json()["errors"][0]["code"] == "E-MIG-010"
    assert approve(w, run_id).status_code == 202
    report = w.core.call("operator", "GET", f"/runs/{run_id}").json()
    assert (report["version"], report["outcome"]) == (4, "SUCCEEDED")
    db = connect(w.core.config.db_path)
    assert kinds(RunStore(db).events(run_id)).count(EventKind.RUN_MIGRATED) == 2
    db.close()


def test_a_service_that_does_not_answer_leaves_the_move_for_the_recovery(
    w: World, tmp_path: Path
) -> None:
    run_id = start_waiting(w)
    service = deploy_service(w, tmp_path, "svc-2", 2)
    w.down.add("svc-2")
    # the fence is taken at the core; the service is away for the import
    answer = migrate(w, run_id, 2, owner="svc-2")
    assert answer.status_code == 503 and answer.json()["errors"][0]["code"] == "E-MIG-011"
    assert approve(w, run_id).json()["errors"][0]["code"] == "E-MIG-010"  # fenced: not queued
    assert w.core.call("operator", "POST", "/migrations/recover").json() == {
        "recovered": {run_id: "unreachable"}
    }
    w.down.clear()
    assert w.core.call("operator", "POST", "/migrations/recover").json() == {
        "recovered": {run_id: "completed"}
    }
    assert service.call(API_KEY, "GET", f"/runs/{run_id}").json()["status"] == "WAITING"
    assert w.core.call("operator", "POST", "/migrations/recover").json() == {"recovered": {}}


def test_the_keys_that_run_a_service_cannot_move_runs_in_or_out(w: World, tmp_path: Path) -> None:
    run_id = start_waiting(w)
    service = deploy_service(w, tmp_path, "svc-2", 2)
    for key in (API_KEY, TITLE_KEY):
        assert service.call(key, "GET", f"/migration/{run_id}/export").status_code == 403
        assert service.call(key, "POST", f"/migration/{run_id}/fence", {}).status_code == 403
    assert service.call(CONTROL, "GET", f"/migration/{run_id}/ownership").json()["state"] == "OWNED"
    assert service.call(None or "", "GET", f"/migration/{run_id}/ownership").status_code == 401


def test_the_core_never_shows_the_control_key(w: World, tmp_path: Path) -> None:
    deploy_service(w, tmp_path, "svc-2", 2)
    listing = w.core.call("admin", "GET", "/deployments")
    one = w.core.call("admin", "GET", "/deployments/svc-2")
    assert CONTROL not in listing.text and CONTROL not in one.text
    assert "control" not in one.text.lower()


# -- the routes of a service, called as the core calls them ----------------------------------------
def plan_body(run_id: str, **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "run_id": run_id, "source": "core", "target": "svc-2", "epoch": 0, "version": 2,
        "inputs": QUARTER["inputs"], "last_seq": 3, "at": 7.0,
    }  # fmt: skip
    return body | changes


def test_a_service_answers_the_calls_of_the_protocol_with_the_codes_they_deserve(
    w: World, tmp_path: Path
) -> None:
    service = deploy_service(w, tmp_path, "svc-2", 2)
    run_id = "svc-2-r1"
    started = service.call(API_KEY, "POST", "/runs", QUARTER)
    assert started.status_code == 202 and started.json()["run_id"] == run_id
    last = len(service.events(run_id)) - 1
    path = f"/migration/{run_id}"
    # unknown run, run in the body that is not the one in the path
    assert service.call(CONTROL, "GET", "/migration/nope/export").status_code == 404
    assert service.call(CONTROL, "POST", f"{path}/fence", plan_body("other")).status_code == 422
    # a fence for an epoch that is not the current one, and for a history that has grown
    stale = service.call(
        CONTROL, "POST", f"{path}/fence", plan_body(run_id, epoch=5, last_seq=last)
    )
    assert stale.status_code == 409 and stale.json()["errors"][0]["code"] == "E-MIG-010"
    grown = service.call(CONTROL, "POST", f"{path}/fence", plan_body(run_id, last_seq=last - 1))
    assert grown.status_code == 409 and "changed" in grown.json()["errors"][0]["message"]
    # the fence, twice (a repeat answers the same), then the service refuses a second target
    first = service.call(CONTROL, "POST", f"{path}/fence", plan_body(run_id, last_seq=last))
    again = service.call(CONTROL, "POST", f"{path}/fence", plan_body(run_id, last_seq=last))
    assert first.status_code == again.status_code == 200 and first.json() == again.json()
    assert first.json()["state"] == "MIGRATING" and first.json()["epoch"] == 1
    other = service.call(
        CONTROL, "POST", f"{path}/fence", plan_body(run_id, target="x", last_seq=last)
    )
    assert other.status_code == 409 and other.json()["errors"][0]["code"] == "E-MIG-010"
    # a release at the wrong epoch is refused, at the right one it is final and repeatable
    assert (
        service.call(CONTROL, "POST", f"{path}/release", plan_body(run_id, epoch=3)).status_code
        == 409
    )
    for _ in range(2):
        out = service.call(CONTROL, "POST", f"{path}/release", plan_body(run_id))
        assert out.status_code == 200 and out.json()["state"] == "MIGRATED_OUT"
    assert service.call(API_KEY, "GET", f"/runs/{run_id}").status_code == 200  # still readable


def test_a_service_refuses_a_snapshot_that_is_not_a_run(w: World, tmp_path: Path) -> None:
    service = deploy_service(w, tmp_path, "svc-2", 2)
    snapshot = {
        "run_id": "r-9", "workflow": NAME, "version": 1, "inputs": {}, "created_at": T,
        "events": [{"seq": 0, "kind": "NOT_A_KIND", "at": 0.0}],
        "ownership": {"run_id": "r-9", "epoch": 0, "state": "OWNED", "peer": None, "version": None},
    }  # fmt: skip
    body = {"snapshot": snapshot, "plan": plan_body("r-9", target="svc-2")}
    refused = service.call(CONTROL, "POST", "/migration/r-9/adopt", body)
    assert refused.status_code == 422 and refused.json()["errors"][0]["field"] == "snapshot"
    assert service.call(CONTROL, "POST", "/migration/other/adopt", body).status_code == 422


def test_the_service_owner_turns_answers_into_the_errors_the_local_owner_raises() -> None:
    from flowstat.migration.handover import Plan
    from flowstat.repo.ownership import NotOwnerError, RunChangedError
    from flowstat.repo.runs import EventConflictError, RunNotFoundError
    from flowstat.service.handover import RunBusyError
    from flowstat.service.remote import ServiceOwner

    plan = Plan("r1", "core", "svc", 0, 2, {}, 3, 1.0)

    def owner(status: int, code: str = "", message: str = "") -> ServiceOwner:
        body: dict[str, object] = {"errors": [{"code": code, "message": message}]}
        return ServiceOwner(lambda method, path, payload: (status, body))

    with pytest.raises(RunNotFoundError):
        owner(404, "E-API-002").ownership("r1")
    with pytest.raises(NotOwnerError):
        owner(409, "E-MIG-010", "fenced").fence(plan)
    with pytest.raises(RunChangedError):
        owner(409, "E-API-003", "the history of r1 changed; check it again").fence(plan)
    with pytest.raises(RunBusyError):
        owner(409, "E-API-003", "the run r1 is being driven right now").fence(plan)
    with pytest.raises(EventConflictError):
        owner(409, "E-API-003", "event 4 differs").release(plan)
    with pytest.raises(OwnerUnavailableError):
        owner(502).release(plan)
    with pytest.raises(RuntimeError, match="refused the call"):
        owner(422, "E-API-001", "bad").release(plan)
    with pytest.raises(NotImplementedError):
        owner(200).retarget(plan)


def test_the_http_transport_reaches_a_server_and_reports_one_that_is_away() -> None:
    import http.server
    import threading

    from flowstat.service.remote import http_transport

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            ok = self.headers.get("Authorization") == "Bearer k"
            payload = b'{"ok": true}' if ok else b'{"errors": [{"code": "E-AUTH-001"}]}'
            self.send_response(200 if ok else 401)
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        assert http_transport(url, "k")("GET", "/x", None) == (200, {"ok": True})
        assert http_transport(url, "wrong")("GET", "/x", None)[0] == 401
    finally:
        server.shutdown()
        server.server_close()
    with pytest.raises(OwnerUnavailableError):
        http_transport(url, "k", timeout=1.0)("GET", "/x", None)  # nobody listens now
