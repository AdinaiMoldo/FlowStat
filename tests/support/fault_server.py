"""A receiver that misbehaves on purpose, for the tests of ``http.call`` (US-17).

It keeps a ledger of the effects it has applied. A request with an ``Idempotency-Key`` has its
effect applied once: the answer is stored under the key and a repeat gets the stored answer (the
header ``Idempotent-Replay: true``); the check, the effect and the store happen under one lock, so
two requests with the same key that arrive together still give one effect. A request without a key
(or any request when ``honour_keys`` is off) has its effect applied every time.

``script`` is the list of faults for the requests in the order they arrive; when it is used up
every request is served normally:

``ok``                     the effect is applied and answered with 200
``drop_response``          the effect is applied and the connection is closed without an answer
``drop_request``           the connection is closed before anything is done (no effect)
``fail_500``               nothing is done and 500 is answered
``fail_500_after_effect``  the effect is applied and 500 is answered (the receiver crashed late)
``bad_request``            nothing is done and 400 is answered
``slow``                   the server waits ``slow_seconds``, then serves the request normally
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType


@dataclass(frozen=True, slots=True)
class Seen:
    """One request as it arrived."""

    key: str | None
    body: object
    fault: str


@dataclass(frozen=True, slots=True)
class Effect:
    key: str | None
    body: object


class FaultServer:
    def __init__(
        self, script: Sequence[str] = (), honour_keys: bool = True, slow_seconds: float = 0.5
    ) -> None:
        self.script = list(script)
        self.honour_keys = honour_keys
        self.slow_seconds = slow_seconds
        self.requests: list[Seen] = []
        self.effects: list[Effect] = []
        self._answers: dict[str, tuple[int, bytes]] = {}
        self._lock = threading.Lock()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                """Silence the default log on stderr."""

            def _serve(self) -> None:
                server._handle(self)

            do_POST = do_PUT = do_PATCH = do_GET = do_DELETE = _serve

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(
            target=lambda: self._httpd.serve_forever(poll_interval=0.01), daemon=True
        )

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}/charge"

    def __enter__(self) -> FaultServer:
        self._thread.start()
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

    # -- one request -----------------------------------------------------------------------
    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        length = int(handler.headers.get("Content-Length") or 0)
        raw = handler.rfile.read(length) if length else b""
        body: object = json.loads(raw) if raw else None
        key = handler.headers.get("Idempotency-Key")
        with self._lock:
            fault = self.script.pop(0) if self.script else "ok"
            self.requests.append(Seen(key, body, fault))
        if fault == "slow":
            time.sleep(self.slow_seconds)
            fault = "ok"
        if fault == "drop_request":
            self._drop(handler)
        elif fault in ("fail_500", "bad_request"):
            status = 500 if fault == "fail_500" else 400
            self._answer(handler, status, {"error": fault})
        else:
            status, payload, replay = self._apply(key, body)
            if fault == "drop_response":
                self._drop(handler)
            elif fault == "fail_500_after_effect":
                self._answer(handler, 500, {"error": "failed after the effect"})
            else:
                self._answer(handler, status, payload, replay)

    def _apply(self, key: str | None, body: object) -> tuple[int, object, bool]:
        """The effect, once per key (under one lock). Returns status, answer and 'a replay'."""
        with self._lock:
            if key is not None and self.honour_keys and key in self._answers:
                status, stored = self._answers[key]
                return status, json.loads(stored), True
            self.effects.append(Effect(key, body))
            answer = {"charged": len(self.effects), "echo": body}
            if key is not None and self.honour_keys:
                self._answers[key] = (200, json.dumps(answer).encode())
            return 200, answer, False

    @staticmethod
    def _answer(
        handler: BaseHTTPRequestHandler, status: int, payload: object, replay: bool = False
    ) -> None:
        data = json.dumps(payload).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        if replay:
            handler.send_header("Idempotent-Replay", "true")
        handler.end_headers()
        handler.wfile.write(data)

    @staticmethod
    def _drop(handler: BaseHTTPRequestHandler) -> None:
        handler.close_connection = True  # nothing is written: the client sees a closed socket
