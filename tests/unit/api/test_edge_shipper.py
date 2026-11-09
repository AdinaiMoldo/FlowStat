"""The real client of the outbox and the thread that runs it: a local server, no mocks of HTTP."""

from __future__ import annotations

import http.server
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest

from flowstat.api.edge import Shipper, http_send
from flowstat.model.events import Event, EventKind
from flowstat.repo.db import open_database
from flowstat.repo.outbox import OutboxStore
from flowstat.repo.runs import RunStore
from flowstat.service.outbox import Reply, SendFailed


class Core(http.server.BaseHTTPRequestHandler):
    status = 200
    answer: ClassVar[dict[str, Any]] = {"run_id": "r1", "stored_through": 1}
    seen: ClassVar[list[tuple[str, str, dict[str, Any]]]] = []

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(length))
        self.seen.append((self.path, self.headers["Authorization"], body))
        raw = json.dumps(self.answer).encode()
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def core() -> Iterator[tuple[str, type[Core]]]:
    handler = type("H", (Core,), {"seen": [], "status": 200, "answer": dict(Core.answer)})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", handler
    server.shutdown()
    server.server_close()


def test_the_batch_is_posted_to_the_deployment_with_the_key(
    core: tuple[str, type[Core]],
) -> None:
    url, handler = core
    reply = http_send(url + "/", "secret", "d-1")({"run": {"run_id": "r1"}, "events": []})
    assert reply == Reply(200, {"run_id": "r1", "stored_through": 1})
    [(path, auth, body)] = handler.seen
    assert (path, auth, body["run"]) == (
        "/deployments/d-1/events",
        "Bearer secret",
        {"run_id": "r1"},
    )


def test_an_error_of_the_core_is_a_reply_and_the_expected_number_is_lifted_out(
    core: tuple[str, type[Core]],
) -> None:
    url, handler = core
    handler.status = 409
    handler.answer = {"errors": [{"code": "E-API-003", "message": "gap", "expected": 5}]}
    reply = http_send(url, "k", "d-1")({})
    assert reply.status == 409 and reply.body["expected"] == 5


def test_an_error_without_a_usable_body_is_still_a_reply(core: tuple[str, type[Core]]) -> None:
    url, handler = core
    handler.status = 503
    handler.answer = {}
    assert http_send(url, "k", "d-1")({}) == Reply(503, {})


def test_no_server_is_a_failed_send_not_a_crash() -> None:
    with pytest.raises(SendFailed):
        http_send("http://127.0.0.1:1", "k", "d-1", timeout=1.0)({})


def test_the_shipper_thread_sends_what_the_log_holds_and_flushes_at_stop(tmp_path: Path) -> None:
    path = str(tmp_path / "service.db")
    db = open_database(path)
    RunStore(db).create("r1", "demo", 1, {}, "2026-11-09T09:00:00Z")
    RunStore(db).append("r1", [Event(0, EventKind.RUN_STARTED, 0.0)])
    sent: list[int] = []
    got = threading.Event()

    def send(payload: dict[str, Any]) -> Reply:
        sent.append(len(payload["events"]))
        got.set()
        return Reply(200, {})

    shipper = Shipper(path, send, idle=0.01)
    shipper.start()
    assert got.wait(5)
    RunStore(db).append("r1", [Event(1, EventKind.RUN_FINISHED, 1.0)])  # just before the stop
    assert shipper.stop(5)
    assert sum(sent) == 2 and OutboxStore(db).shipped_through("r1") == 1
