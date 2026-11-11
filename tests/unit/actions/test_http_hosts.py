"""The hosts ``http.call`` may reach (US-39, docs/dsl-spec.md section 5.5).

With a list, a call to another host fails before anything is sent, a redirect to another host is
not followed, and the failure is permanent (a retry would fail the same way). Without a list the
action is as it was.
"""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from flowstat.actions.base import ActionContext
from flowstat.actions.registry import default_registry
from flowstat.engine.runner import Error, Ok
from flowstat.engine.scheduler import Start
from tests.support.fault_server import FaultServer

CTX = ActionContext("run-1", "pay", Path("."), None)
LOCAL = frozenset({"127.0.0.1"})


def call(url: str, hosts: frozenset[str] | None, **args: object) -> object:
    registry = default_registry(hosts)
    return registry.executor(CTX)(Start("pay", "http.call", {"url": url, **args}, 1, 0.0, False))


def test_a_host_on_the_list_is_called() -> None:
    with FaultServer() as server:
        result = call(server.url, LOCAL)
    assert isinstance(result, Ok) and result.output["status"] == 200


def test_a_host_that_is_not_on_the_list_fails_before_anything_is_sent() -> None:
    with FaultServer() as server:
        result = call(server.url, frozenset({"api.example.kg"}))
        assert server.requests == []  # nothing reached the server
    assert isinstance(result, Error)
    assert result.code == "E-ACT-011" and not result.transient
    assert "127.0.0.1" in result.message and "api.example.kg" in result.message


def test_an_empty_list_cannot_be_made_so_no_list_means_any_host() -> None:
    with FaultServer() as server:
        result = call(server.url, None)
    assert isinstance(result, Ok)


def test_the_check_is_on_the_host_not_on_the_text_of_the_url() -> None:
    with FaultServer() as server:
        trick = server.url.replace("127.0.0.1", "127.0.0.1@localhost")  # the host is localhost
        result = call(trick, LOCAL)
        assert server.requests == []
    assert isinstance(result, Error) and result.code == "E-ACT-011"


def test_a_url_without_a_host_is_a_bad_argument_not_a_forbidden_host() -> None:
    result = call("ftp://127.0.0.1/x", LOCAL)
    assert isinstance(result, Error) and result.code == "E-ACT-009"


@pytest.fixture
def redirector() -> Iterator[tuple[str, str]]:
    """A server at 127.0.0.1 that answers every request with a redirect to ``localhost``."""
    target = FaultServer()
    target.__enter__()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.send_response(302)
            self.send_header("Location", target.url.replace("127.0.0.1", "localhost"))
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", target.url
    server.shutdown()
    server.server_close()
    target.__exit__(None, None, None)


def test_a_redirect_to_a_host_that_is_not_on_the_list_is_not_followed(
    redirector: tuple[str, str],
) -> None:
    url, _ = redirector
    result = call(url, LOCAL)
    assert isinstance(result, Error) and result.code == "E-ACT-011" and not result.transient
    assert "localhost" in result.message


def test_a_redirect_to_a_host_on_the_list_is_followed(redirector: tuple[str, str]) -> None:
    url, _ = redirector
    result = call(url, frozenset({"127.0.0.1", "localhost"}))
    assert isinstance(result, Ok)


def test_registered_with_the_list_the_signature_carries_it() -> None:
    sig = default_registry(LOCAL).signatures()["http.call"]
    assert sig.hosts == LOCAL
    assert default_registry().signatures()["http.call"].hosts is None
