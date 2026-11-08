"""Tests of the launchers with a fake process, a fake clock and a fake Docker client."""

from __future__ import annotations

import http.server
import os
import subprocess
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from flowstat.deploy.launchers import (
    DockerLauncher,
    ProcessLauncher,
    free_port,
    http_healthy,
    tail,
)
from flowstat.service.deployments import LaunchError


class Clock:
    """Time that only moves when something sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeProcess:
    def __init__(self, polls: list[int | None], pid: int = 4242) -> None:
        self.polls = polls
        self.pid = pid
        self.terminated = False
        self.killed = False
        self.waited = False
        self.refuse_terminate = False

    def poll(self) -> int | None:
        if self.terminated or self.killed:
            return -15
        return self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        if self.refuse_terminate and not self.killed:
            raise subprocess.TimeoutExpired("x", timeout or 0)
        return 0


def launcher(
    process: FakeProcess,
    healthy: Callable[[str], bool],
    clock: Clock,
    log_text: str = "",
    seen: list[Any] | None = None,
) -> ProcessLauncher:
    def popen(command: list[str], **options: Any) -> FakeProcess:
        options["stdout"].write(log_text.encode())
        if seen is not None:
            seen.append(command)
        return process

    return ProcessLauncher(
        python="py",
        port=lambda: 9100,
        healthy=healthy,
        clock=clock,
        sleep=clock.sleep,
        popen=popen,
        limit=20.0,
        interval=0.5,
    )


def test_a_process_is_started_on_the_port_and_waited_for_until_it_answers(tmp_path: Path) -> None:
    clock = Clock()
    calls: list[tuple[str, float]] = []
    seen: list[Any] = []

    def healthy(endpoint: str) -> bool:
        calls.append((endpoint, clock.now))
        return len(calls) == 3

    process = FakeProcess([None])
    running = launcher(process, healthy, clock, seen=seen).start(tmp_path)
    assert running.endpoint == "http://127.0.0.1:9100" and running.handle == "4242"
    assert seen == [
        ["py", "-m", "flowstat.api.service", "--bundle", str(tmp_path), "--port", "9100"]
    ]
    assert calls == [
        ("http://127.0.0.1:9100", 0.0),
        ("http://127.0.0.1:9100", 0.5),
        ("http://127.0.0.1:9100", 1.0),
    ]
    assert not process.terminated and (tmp_path / "service.log").exists()


def test_a_process_that_exits_is_a_launch_error_with_the_end_of_its_log(tmp_path: Path) -> None:
    clock = Clock()
    process = FakeProcess([None, 3])
    log = "\n".join(f"line {n}" for n in range(30))
    with pytest.raises(LaunchError) as info:
        launcher(process, lambda e: False, clock, log_text=log).start(tmp_path)
    assert "exited with code 3" in str(info.value)
    assert (
        info.value.detail.splitlines()[-1] == "line 29"
        and len(info.value.detail.splitlines()) == 20
    )
    assert process.waited


def test_a_process_that_never_answers_is_ended_after_the_limit(tmp_path: Path) -> None:
    clock = Clock()
    process = FakeProcess([None])
    with pytest.raises(LaunchError, match="did not answer within 20 s"):
        launcher(process, lambda e: False, clock).start(tmp_path)
    assert process.terminated and clock.now >= 20.0


def test_a_process_is_ended_when_the_wait_itself_fails(tmp_path: Path) -> None:
    clock = Clock()
    process = FakeProcess([None])

    def broken(endpoint: str) -> bool:
        raise RuntimeError("the check broke")

    with pytest.raises(RuntimeError):
        launcher(process, broken, clock).start(tmp_path)
    assert process.terminated  # never left behind


def test_a_process_that_will_not_stop_is_killed(tmp_path: Path) -> None:
    clock = Clock()
    process = FakeProcess([None])
    process.refuse_terminate = True
    with pytest.raises(LaunchError):
        launcher(process, lambda e: False, clock).start(tmp_path)
    assert process.terminated and process.killed


def test_stop_ends_a_process_this_launcher_started(tmp_path: Path) -> None:
    clock = Clock()
    process = FakeProcess([None])
    started = launcher(process, lambda e: True, clock)
    running = started.start(tmp_path)
    started.stop(running.handle)
    assert process.terminated


def test_stop_by_pid_when_the_core_was_restarted(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))
    ProcessLauncher().stop("777")
    assert sent and sent[0][0] == 777

    def gone(pid: int, sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", gone)
    ProcessLauncher().stop("777")  # already gone: nothing to do

    def denied(pid: int, sig: int) -> None:
        raise PermissionError("not yours")

    monkeypatch.setattr(os, "kill", denied)
    with pytest.raises(LaunchError, match="cannot stop process 777"):
        ProcessLauncher().stop("777")
    with pytest.raises(LaunchError):
        ProcessLauncher().stop("not a pid")


def test_free_port_gives_a_port_that_can_be_bound() -> None:
    import socket

    port = free_port()
    assert 0 < port < 65536
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))


@pytest.fixture
def health_server() -> Iterator[str]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200 if self.path == "/health" else 404)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_the_health_check_is_true_for_a_server_that_answers_and_false_otherwise(
    health_server: str,
) -> None:
    assert http_healthy(health_server)
    assert not http_healthy(f"http://127.0.0.1:{free_port()}")  # nobody listens there
    assert not http_healthy("not a url")


def test_tail_of_a_missing_file_is_empty(tmp_path: Path) -> None:
    assert tail(tmp_path / "none.log") == ""


# -- Docker, against a fake client -------------------------------------------------------------
class FakeContainer:
    def __init__(self, statuses: list[str], logs: bytes = b"") -> None:
        self.statuses = statuses
        self.id = "c0ffee"
        self.status = statuses[0]
        self._logs = logs
        self.stopped = False
        self.removed = False

    def reload(self) -> None:
        self.status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    def logs(self, tail: int = 0) -> bytes:
        return self._logs

    def stop(self, timeout: int = 0) -> None:
        self.stopped = True

    def remove(self) -> None:
        self.removed = True


class FakeClient:
    def __init__(
        self, container: FakeContainer, fail: str | None = None, down: bool = False
    ) -> None:
        self.container = container
        self.fail = fail
        self.down = down
        self.built: list[dict[str, Any]] = []
        self.ran: list[tuple[str, dict[str, Any]]] = []
        self.images = self
        self.containers = self

    def ping(self) -> bool:
        if self.down:
            raise ConnectionError("no such file: /var/run/docker.sock")
        return True

    def build(self, **options: Any) -> None:
        if self.fail == "build":
            raise RuntimeError("pip could not find a version")
        self.built.append(options)

    def run(self, image: str, **options: Any) -> FakeContainer:
        self.ran.append((image, options))
        return self.container

    def get(self, handle: str) -> FakeContainer:
        if handle != self.container.id:
            raise KeyError(handle)
        return self.container


def docker(client: FakeClient, clock: Clock, healthy: Callable[[str], bool]) -> DockerLauncher:
    return DockerLauncher(
        client=lambda: client, port=lambda: 9200, healthy=healthy, clock=clock, sleep=clock.sleep,
        limit=10.0, interval=1.0,
    )  # fmt: skip


def test_the_image_is_built_the_container_run_with_the_bundle_mounted_and_waited_for(
    tmp_path: Path,
) -> None:
    clock, container = Clock(), FakeContainer(["created", "running"])
    client = FakeClient(container)
    bundle = tmp_path / "S-7"
    running = docker(client, clock, lambda e: clock.now >= 2.0).start(bundle)
    assert running.endpoint == "http://127.0.0.1:9200" and running.handle == "c0ffee"
    assert client.built == [{"path": str(bundle), "tag": "flowstat-service-s-7", "rm": True}]
    [(image, options)] = client.ran
    assert image == "flowstat-service-s-7" and options["detach"] is True
    assert options["ports"] == {"8000/tcp": ("127.0.0.1", 9200)}  # published on loopback only
    assert options["volumes"] == {str(bundle): {"bind": "/service/bundle", "mode": "rw"}}


def test_a_docker_that_cannot_be_reached_is_said_so(tmp_path: Path) -> None:
    client = FakeClient(FakeContainer(["running"]), down=True)
    with pytest.raises(LaunchError, match="Docker cannot be reached") as info:
        docker(client, Clock(), lambda e: True).start(tmp_path / "s")
    assert "docker.sock" in info.value.detail and client.built == []


def test_a_failed_build_is_a_launch_error_with_the_reason(tmp_path: Path) -> None:
    client = FakeClient(FakeContainer(["running"]), fail="build")
    with pytest.raises(LaunchError) as info:
        docker(client, Clock(), lambda e: True).start(tmp_path / "s")
    assert "pip could not find a version" in info.value.detail and client.ran == []


def test_a_container_that_exits_is_removed_and_its_log_is_the_reason(tmp_path: Path) -> None:
    container = FakeContainer(["created", "exited"], logs=b"Traceback ...\nValueError: boom")
    with pytest.raises(LaunchError) as info:
        docker(FakeClient(container), Clock(), lambda e: False).start(tmp_path / "s")
    assert "ValueError: boom" in info.value.detail
    assert container.stopped and container.removed


def test_a_container_that_never_answers_is_removed_after_the_limit(tmp_path: Path) -> None:
    clock, container = Clock(), FakeContainer(["running"])
    with pytest.raises(LaunchError, match="did not answer"):
        docker(FakeClient(container), clock, lambda e: False).start(tmp_path / "s")
    assert clock.now >= 10.0 and container.removed


def test_stop_removes_the_container_and_an_unknown_one_is_an_error() -> None:
    container = FakeContainer(["running"])
    launcher_ = docker(FakeClient(container), Clock(), lambda e: True)
    launcher_.stop("c0ffee")
    assert container.stopped and container.removed
    with pytest.raises(LaunchError, match="cannot stop container nope"):
        launcher_.stop("nope")
