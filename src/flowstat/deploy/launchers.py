"""Start a bundle as a process or as a container, and wait until it answers.

Everything that touches the operating system is here: ``subprocess``, ``socket`` and the Docker
client. The clock, the sleep, the check of readiness, the way a process is started and the Docker
client are arguments, so the tests run all of it without starting anything (deterministic mode).
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from flowstat.service.deployments import Launcher, LaunchError, Running

READY_LIMIT = 20.0  # seconds a process has to answer /health
CONTAINER_READY_LIMIT = 90.0  # an image may have to be built and its packages installed first
LOG = "service.log"


def free_port() -> int:
    """A port of 127.0.0.1 that nobody holds at this moment (found by binding port 0)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def http_healthy(endpoint: str) -> bool:
    try:
        with urllib.request.urlopen(endpoint + "/health", timeout=1.0) as response:
            return bool(response.status == 200)
    except (urllib.error.URLError, OSError, ValueError):
        return False


def tail(path: Path, lines: int = 20) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


class ProcessLauncher:
    """``python -m flowstat.api.service`` on a free port of 127.0.0.1."""

    def __init__(
        self,
        python: str = sys.executable,
        port: Callable[[], int] = free_port,
        healthy: Callable[[str], bool] = http_healthy,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        popen: Callable[..., Any] = subprocess.Popen,
        limit: float = READY_LIMIT,
        interval: float = 0.1,
    ) -> None:
        self.python, self.port, self.healthy = python, port, healthy
        self.clock, self.sleep, self.popen = clock, sleep, popen
        self.limit, self.interval = limit, interval
        self._started: dict[str, Any] = {}

    def start(self, bundle: Path) -> Running:
        number = self.port()
        command = [
            self.python, "-m", "flowstat.api.service",
            "--bundle", str(bundle), "--port", str(number),
        ]  # fmt: skip
        with open(bundle / LOG, "wb") as log:
            process = self.popen(
                command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT
            )
        endpoint = f"http://127.0.0.1:{number}"
        try:
            self._wait(process, endpoint, bundle)
        except BaseException:
            self._end(process)  # a process is never left behind by a failed start
            raise
        self._started[str(process.pid)] = process
        return Running(endpoint, str(process.pid))

    def _wait(self, process: Any, endpoint: str, bundle: Path) -> None:
        deadline = self.clock() + self.limit
        while self.clock() < deadline:
            code = process.poll()
            if code is not None:
                raise LaunchError(f"the service exited with code {code}", tail(bundle / LOG))
            if self.healthy(endpoint):
                return
            self.sleep(self.interval)
        raise LaunchError(f"the service did not answer within {self.limit:g} s", tail(bundle / LOG))

    def stop(self, handle: str) -> None:
        process = self._started.pop(handle, None)
        if process is not None:
            self._end(process)
            return
        try:  # started by an earlier run of the core: only its pid is known
            os.kill(int(handle), signal.SIGTERM)
        except ProcessLookupError:
            pass  # already gone
        except (ValueError, PermissionError, OSError) as error:
            raise LaunchError(f"cannot stop process {handle}: {error}") from error

    @staticmethod
    def _end(process: Any) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        else:
            process.wait()


class DockerLauncher:
    """Build the image of a bundle and run it, with the bundle directory mounted into it."""

    def __init__(
        self,
        client: Callable[[], Any] | None = None,
        port: Callable[[], int] = free_port,
        healthy: Callable[[str], bool] = http_healthy,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        limit: float = CONTAINER_READY_LIMIT,
        interval: float = 0.5,
    ) -> None:
        self.client = client or _docker_client
        self.port, self.healthy = port, healthy
        self.clock, self.sleep = clock, sleep
        self.limit, self.interval = limit, interval

    def start(self, bundle: Path) -> Running:
        try:
            client = self.client()
            client.ping()
        except Exception as error:
            raise LaunchError(
                "Docker cannot be reached", f"Docker cannot be reached: {error}"
            ) from error
        tag = f"flowstat-service-{bundle.name}".lower()
        number = self.port()
        try:
            client.images.build(path=str(bundle), tag=tag, rm=True)
            container = client.containers.run(
                tag,
                detach=True,
                name=tag,
                ports={"8000/tcp": ("127.0.0.1", number)},
                volumes={str(bundle): {"bind": "/service/bundle", "mode": "rw"}},
            )
        except Exception as error:
            raise LaunchError("the container could not be built or started", str(error)) from error
        endpoint = f"http://127.0.0.1:{number}"
        deadline = self.clock() + self.limit
        while self.clock() < deadline:
            container.reload()
            if container.status in ("exited", "dead"):
                break
            if self.healthy(endpoint):
                return Running(endpoint, str(container.id))
            self.sleep(self.interval)
        logs = container.logs(tail=20)
        text = logs.decode("utf8", "replace") if isinstance(logs, bytes) else str(logs)
        self._remove(container)
        raise LaunchError("the container did not answer", text)

    def stop(self, handle: str) -> None:
        try:
            self._remove(self.client().containers.get(handle))
        except LaunchError:
            raise
        except Exception as error:
            raise LaunchError(f"cannot stop container {handle}", str(error)) from error

    @staticmethod
    def _remove(container: Any) -> None:
        container.stop(timeout=10)
        container.remove()


def _docker_client() -> Any:
    import docker  # the Docker SDK is installed with the `deploy` extra

    return docker.from_env()


def launchers_for() -> Mapping[str, Launcher]:
    return {"process": ProcessLauncher(), "docker": DockerLauncher()}
