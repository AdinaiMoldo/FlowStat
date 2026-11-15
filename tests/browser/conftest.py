"""A real server and a real browser for the tests of the page (marker ``browser``).

The page is the one served by the core; the editor is the Monaco that ``scripts/fetch_ui.py``
fetched. Where Playwright, Chromium or the fetched editor is missing the tests skip (CI on Linux
installs all three and fails if they skip).
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from flowstat.api.ui import UI_DIR
from tests.unit.api.helpers import Env, make_env

pytest.importorskip("playwright.sync_api", reason="Playwright is not installed")
import uvicorn
from playwright.sync_api import Browser, sync_playwright

VENDOR = UI_DIR / "vendor" / "VERSIONS.json"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class Server:
    def __init__(self, env: Env, url: str) -> None:
        self.env = env
        self.url = url


@pytest.fixture(scope="session")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    env = make_env(Path(tmp_path_factory.mktemp("browser")))
    port = free_port()
    config = uvicorn.Config(env.client.app, host="127.0.0.1", port=port, log_level="warning")
    instance = uvicorn.Server(config)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    for _ in range(100):
        if instance.started:
            break
        time.sleep(0.05)
    else:
        pytest.fail("the server did not start")
    yield Server(env, f"http://127.0.0.1:{port}")
    instance.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    try:
        playwright = sync_playwright().start()
    except Exception as error:  # the driver of Playwright cannot start (a missing system library)
        pytest.skip(f"Playwright cannot be started: {error}")
    try:
        try:
            launched = playwright.chromium.launch()
        except Exception as error:  # the browser is not installed
            pytest.skip(f"Chromium cannot be started: {error}")
        yield launched
        launched.close()
    finally:
        playwright.stop()


@pytest.fixture
def need_editor() -> None:
    if not VENDOR.is_file():
        pytest.skip("the editor was not fetched: python scripts/fetch_ui.py")
