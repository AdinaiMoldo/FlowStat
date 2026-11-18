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
from contextlib import contextmanager
from pathlib import Path

import pytest
from starlette.types import ASGIApp

from flowstat.api.ui import UI_DIR
from tests.unit.api.helpers import Env, make_env

pytest.importorskip("playwright.sync_api", reason="Playwright is not installed")
import uvicorn
from playwright.sync_api import Browser, Page, sync_playwright

VENDOR = UI_DIR / "vendor" / "VERSIONS.json"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class Server:
    def __init__(self, env: Env, url: str) -> None:
        self.env = env
        self.url = url


@contextmanager
def serving(app: ASGIApp) -> Iterator[str]:
    """Run ``app`` on a free port of the loopback address; the address while the block runs."""
    port = free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    instance = uvicorn.Server(config)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    for _ in range(100):
        if instance.started:
            break
        time.sleep(0.05)
    else:
        pytest.fail("the server did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        instance.should_exit = True
        thread.join(timeout=10)


def use_token(page: Page, token: str, view: str = "editor") -> None:
    """Sign in with an API token (the form under "Use an API token instead") and open ``view``.

    The sign-in page takes a name and a password; a token is the way in that needs neither, so
    the tests that are about something else than signing in use it.
    """
    if page.is_hidden("#token"):
        page.click("#token-toggle")
    page.fill("#token", token)
    page.click("#sign-in button[type=submit]")
    page.wait_for_function("document.body.dataset.view !== 'login'")
    page.evaluate("view => { location.hash = '#/' + view; }", view)
    page.wait_for_function("view => document.body.dataset.view === view", arg=view)


def sign_out(page: Page) -> None:
    """Sign out; the page is loaded again, so this returns when the new page is ready."""
    with page.expect_event("load"):
        page.click("#sign-out")
    page.wait_for_selector("body[data-ready]")


@pytest.fixture(scope="session")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    env = make_env(Path(tmp_path_factory.mktemp("browser")))
    with serving(env.client.app) as url:
        yield Server(env, url)


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
