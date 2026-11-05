"""Tests of ``http.call`` (US-17): classification of answers and the key it sends."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from flowstat.actions.base import ActionContext
from flowstat.actions.http import decode
from flowstat.actions.registry import default_registry
from flowstat.engine.runner import Error, Ok
from flowstat.engine.scheduler import Start
from tests.support.fault_server import FaultServer

CTX = ActionContext("run-1", "pay", Path("."), None)


def call(url: str, attempt: int = 1, compensation: bool = False, **args: object) -> object:
    registry = default_registry()
    command = Start("pay", "http.call", {"url": url, **args}, attempt, 0.0, compensation)
    return registry.executor(CTX)(command)


def test_a_200_answer_is_the_output() -> None:
    with FaultServer() as server:
        result = call(server.url, body={"amount": 5})

    assert isinstance(result, Ok)
    assert result.output["status"] == 200
    assert result.output["body"] == {"charged": 1, "echo": {"amount": 5}}


def test_the_key_of_the_operation_is_sent_and_is_the_same_for_every_attempt() -> None:
    with FaultServer() as server:
        call(server.url, attempt=1)
        call(server.url, attempt=2)
        call(server.url, attempt=1, compensation=True)

    assert [r.key for r in server.requests] == [
        "run-1/pay",
        "run-1/pay",
        "run-1/pay/compensate",
    ]


def test_the_method_and_the_body_are_used() -> None:
    with FaultServer() as server:
        result = call(server.url, method="PUT", body=[1, 2])

    assert isinstance(result, Ok) and server.requests[0].body == [1, 2]


def test_a_get_has_no_body() -> None:
    with FaultServer() as server:
        result = call(server.url, method="GET")

    assert isinstance(result, Ok) and server.requests[0].body is None


def test_a_4xx_answer_is_permanent() -> None:
    with FaultServer(["bad_request"]) as server:
        result = call(server.url)

    assert isinstance(result, Error)
    assert (result.code, result.transient) == ("E-ACT-010", False)
    assert "400" in result.message


def test_a_5xx_answer_is_transient() -> None:
    with FaultServer(["fail_500"]) as server:
        result = call(server.url)

    assert isinstance(result, Error)
    assert (result.code, result.transient) == ("E-ACT-008", True)
    assert "500" in result.message


def test_a_dropped_connection_is_transient() -> None:
    with FaultServer(["drop_request"]) as server:
        result = call(server.url)

    assert isinstance(result, Error)
    assert (result.code, result.transient) == ("E-ACT-008", True)


def test_a_dropped_response_is_transient_although_the_effect_exists() -> None:
    with FaultServer(["drop_response"]) as server:
        result = call(server.url)

    assert isinstance(result, Error) and result.transient
    assert len(server.effects) == 1


def test_a_timeout_is_transient() -> None:
    with FaultServer(["slow"], slow_seconds=1.0) as server:
        result = call(server.url, timeout=0.1)

    assert isinstance(result, Error)
    assert (result.code, result.transient) == ("E-ACT-008", True)
    assert "timed out" in result.message


def test_a_server_that_is_not_there_is_transient() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]  # nothing listens here once the socket is closed

    result = call(f"http://127.0.0.1:{port}/x")

    assert isinstance(result, Error) and result.code == "E-ACT-008" and result.transient


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.org/x", "nonsense"])
def test_only_http_and_https_are_allowed(url: str) -> None:
    result = call(url)

    assert isinstance(result, Error) and result.code == "E-ACT-009" and not result.transient


def test_an_unknown_method_is_refused() -> None:
    result = call("http://127.0.0.1:1/x", method="TRACE")

    assert isinstance(result, Error) and result.code == "E-ACT-009"


@pytest.mark.parametrize("timeout", [0, -1, "soon", True])
def test_a_bad_timeout_is_refused(timeout: object) -> None:
    result = call("http://127.0.0.1:1/x", timeout=timeout)

    assert isinstance(result, Error) and result.code == "E-ACT-009"


def test_a_missing_url_is_an_argument_error() -> None:
    command = Start("pay", "http.call", {}, 1)

    result = default_registry().executor(CTX)(command)

    assert isinstance(result, Error) and result.code == "E-ACT-009"


def test_an_answer_that_is_not_json_is_kept_as_text() -> None:
    assert decode(b"plain text") == "plain text"
    assert decode(b"") is None
    assert decode(b'{"a": 1}') == {"a": 1}


def test_http_call_is_registered_and_idempotent() -> None:
    registry = default_registry()

    assert "http.call" in registry.names() and registry.is_idempotent("http.call")
