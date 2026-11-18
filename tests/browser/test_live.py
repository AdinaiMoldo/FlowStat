"""US-42 in a browser: the table of runs follows the server while the page is open and visible.

The page asks again every few seconds while the Runs view is the one shown and the tab is visible,
and asks nothing while the tab is hidden or another view is shown. ``?live=MS`` makes the period
short for the tests; without it the period is the default, which the page publishes in
``data-live-ms`` so that a test can check that it is within the 5 s of the story.
"""

from __future__ import annotations

import sqlite3
import time

import pytest
from playwright.sync_api import Browser, Page, Request

from flowstat.model.events import Event, EventKind
from flowstat.model.states import RunOutcome
from flowstat.repo.runs import RunStore
from tests.browser.conftest import Server, use_token
from tests.unit.api.helpers import EXAMPLE, NAME, TOKENS, T

pytestmark = pytest.mark.browser

OWN = "live_workflow"  # the server is shared with other modules: a workflow of its own


def ensure_published(server: Server) -> None:
    """Version 1 of the example, which a run that is opened needs (the report reads its plan)."""
    env = server.env
    if env.call("designer", "GET", f"/workflows/{NAME}/versions/1").status_code != 200:
        env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
        env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish")


def store(server: Server) -> tuple[sqlite3.Connection, RunStore]:
    db = sqlite3.connect(server.env.config.db_path, isolation_level=None)
    return db, RunStore(db)


def open_runs(browser: Browser, server: Server, live: int | None = 200) -> Page:
    page = browser.new_page()
    query = "?plain" + (f"&live={live}" if live is not None else "")
    page.goto(f"{server.url}/ui/{query}")
    page.wait_for_selector("body[data-ready]")
    use_token(page, TOKENS["operator"])
    page.click("#tab-runs")
    page.wait_for_function("document.getElementById('runs-status').textContent.includes('run(s)')")
    return page


def hide(page: Page, hidden: bool) -> None:
    page.evaluate(
        """(hidden) => {
            Object.defineProperty(document, 'visibilityState',
                {configurable: true, get: () => (hidden ? 'hidden' : 'visible')});
            document.dispatchEvent(new Event('visibilitychange'));
        }""",
        hidden,
    )


def counted(page: Page) -> list[str]:
    """The requests of the page to the list of runs, from now on. The sync API of Playwright
    delivers events while a call into it waits, so the tests wait with ``wait_for_timeout`` and
    never with ``time.sleep``, or the lists they compare would be empty for the wrong reason."""
    seen: list[str] = []

    def note(request: Request) -> None:
        if request.method == "GET" and request.url.split("?")[0].endswith("/runs"):
            seen.append(request.url)

    page.on("request", note)
    return seen


def test_a_run_that_appears_and_a_run_that_changes_are_shown_without_a_click(
    browser: Browser, server: Server
) -> None:
    page = open_runs(browser, server)
    db, runs = store(server)
    runs.create("live-1", OWN, 1, {}, T)
    row = page.locator("[data-testid=run][data-id=live-1]")
    row.wait_for(timeout=5000)  # it was not there when the page was opened
    assert "CREATED" in row.inner_text()
    runs.append(
        "live-1",
        [
            Event(0, EventKind.RUN_STARTED, 0.0),
            Event(1, EventKind.RUN_FINISHED, 1.0, outcome=RunOutcome.SUCCEEDED),
        ],
    )
    page.wait_for_function(
        "document.querySelector('[data-testid=run][data-id=live-1]').innerText.includes('SUCCEEDED')",
        timeout=5000,
    )
    db.close()
    page.close()


def test_the_run_that_is_open_and_the_choice_of_source_stay_as_they_were(
    browser: Browser, server: Server
) -> None:
    ensure_published(server)
    page = open_runs(browser, server)
    db, runs = store(server)
    runs.create("live-open", NAME, 1, {}, T)
    row = page.locator("[data-testid=run][data-id=live-open]")
    row.wait_for(timeout=5000)
    row.click()
    page.wait_for_function("document.getElementById('run-title').dataset.runId === 'live-open'")
    page.select_option("#runs-from", "core")
    page.wait_for_timeout(1000)  # several rounds of the refresh
    assert page.locator("#runs-from").input_value() == "core"
    assert page.locator("[data-testid=run][data-id=live-open].current").count() == 1
    assert page.locator("#run-title").get_attribute("data-run-id") == "live-open"
    db.close()
    page.close()


def test_a_hidden_tab_asks_nothing_and_asks_at_once_when_it_is_shown_again(
    browser: Browser, server: Server
) -> None:
    page = open_runs(browser, server)
    hide(page, True)
    page.wait_for_timeout(300)  # a request that was already under way may still arrive
    seen = counted(page)
    page.wait_for_timeout(1500)  # more than seven periods
    assert seen == []
    hide(page, False)
    deadline = time.time() + 2
    while not seen and time.time() < deadline:
        page.wait_for_timeout(50)
    assert seen, "the page did not ask when the tab became visible"
    page.close()


def test_another_view_asks_nothing_and_coming_back_asks_again(
    browser: Browser, server: Server
) -> None:
    page = open_runs(browser, server)
    page.click("#tab-services")  # the operator has no Statistics; Services does not read the runs
    page.wait_for_function("document.body.dataset.view === 'services'")
    page.wait_for_timeout(300)
    seen = counted(page)
    page.wait_for_timeout(1200)
    assert seen == []
    page.click("#tab-runs")
    page.wait_for_function("document.body.dataset.view === 'runs'")
    deadline = time.time() + 2
    while not seen and time.time() < deadline:
        page.wait_for_timeout(50)
    assert seen
    page.close()


def test_a_refresh_that_fails_keeps_the_table_and_the_next_one_repairs_it(
    browser: Browser, server: Server
) -> None:
    page = open_runs(browser, server)
    db, runs = store(server)
    runs.create("live-keep", OWN, 1, {}, T)
    page.locator("[data-testid=run][data-id=live-keep]").wait_for(timeout=5000)
    page.route("**/runs", lambda route: route.abort())
    page.wait_for_function(
        "document.getElementById('runs-status').textContent.includes('cannot be reached')",
        timeout=5000,
    )
    assert page.locator("[data-testid=run][data-id=live-keep]").count() == 1  # still there
    page.unroute("**/runs")
    page.wait_for_function(
        "document.getElementById('runs-status').textContent.includes('run(s)')", timeout=5000
    )
    db.close()
    page.close()


def test_a_page_without_a_token_does_not_ask_at_all_and_comes_back_after_signing_in(
    browser: Browser, server: Server
) -> None:
    page = browser.new_page()
    seen = counted(page)
    page.goto(f"{server.url}/ui/?plain&live=200#/runs")
    page.wait_for_selector("body[data-ready]")
    assert page.evaluate("document.body.dataset.view") == "login"  # the guard sent it here
    page.wait_for_timeout(1500)
    assert seen == []  # nobody is signed in, so the runs are not asked for
    page.click("#token-toggle")
    page.fill("#token", TOKENS["operator"])
    page.click("#sign-in button[type=submit]")
    page.wait_for_function("document.getElementById('runs-status').textContent.includes('run(s)')")
    assert page.evaluate("document.body.dataset.view") == "runs"  # the page that was wanted
    page.close()


def test_the_default_period_is_within_the_five_seconds_of_the_story(
    browser: Browser, server: Server
) -> None:
    page = open_runs(browser, server, live=None)
    period = int(page.locator("#runs-status").get_attribute("data-live-ms") or "0")
    assert 0 < period <= 4500  # a change waits for one period and for one answer
    page.close()


def test_the_services_view_shows_a_service_that_stops_answering_without_a_click(
    browser: Browser, server: Server
) -> None:
    from flowstat.repo.deployments import Deployment, DeploymentStore

    db = sqlite3.connect(server.env.config.db_path, isolation_level=None)
    DeploymentStore(db).add(
        Deployment(
            "svc-live", NAME, 1, "process", "running", "/bundles/svc-live", T, T,
            endpoint="http://127.0.0.1:9101", answering=True, answering_since=T,
        )
    )  # fmt: skip
    page = browser.new_page()
    page.goto(f"{server.url}/ui/?plain&live=200")
    page.wait_for_selector("body[data-ready]")
    use_token(page, TOKENS["operator"])
    page.click("#tab-services")
    row = page.locator("[data-testid=service][data-id=svc-live]")
    row.wait_for(timeout=5000)
    assert "running" in row.inner_text()
    db.execute("UPDATE deployments SET answering = 0 WHERE id = 'svc-live'")
    page.wait_for_function(
        "document.querySelector('[data-testid=service][data-id=svc-live]').innerText"
        ".includes('unreachable')",
        timeout=5000,
    )
    seen = counted_deployments(page)
    page.click("#tab-editor")  # another view: the services are no longer asked for
    page.wait_for_timeout(300)
    before = len(seen)
    page.wait_for_timeout(1000)
    assert len(seen) == before
    db.close()
    page.close()


def counted_deployments(page: Page) -> list[str]:
    seen: list[str] = []

    def note(request: Request) -> None:
        if request.method == "GET" and request.url.split("?")[0].endswith("/deployments"):
            seen.append(request.url)

    page.on("request", note)
    return seen
