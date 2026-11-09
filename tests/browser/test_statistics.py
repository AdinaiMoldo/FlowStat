"""US-34 in a browser: the table of failure rate and duration per workflow."""

from __future__ import annotations

import sqlite3

import pytest
from playwright.sync_api import Browser, Page

from flowstat.model.events import Event, EventKind
from flowstat.model.states import RunOutcome
from flowstat.repo.runs import RunStore
from tests.browser.conftest import Server
from tests.unit.api.helpers import TOKENS, T

pytestmark = pytest.mark.browser

OWN = "statistics_workflow"  # the server is shared with other modules: a workflow of its own
TRAP = '<img src=x onerror="window.pwned = 1">'


@pytest.fixture(scope="module")
def ten_runs(server: Server) -> None:
    db = sqlite3.connect(server.env.config.db_path, isolation_level=None)
    store = RunStore(db)
    outcomes = [RunOutcome.SUCCEEDED] * 8 + [RunOutcome.FAILED, RunOutcome.COMPENSATED]
    for i, outcome in enumerate(outcomes):
        run_id = f"stat-{i:02d}"
        store.create(run_id, OWN, 1 if i < 5 else 2, {}, T)
        store.append(
            run_id,
            [
                Event(0, EventKind.RUN_STARTED, 0.0),
                Event(1, EventKind.RUN_FINISHED, 12.5, outcome=outcome),
            ],
        )
    store.create("stat-trap", TRAP, 1, {}, T)  # a name with markup must stay text
    db.close()


def open_page(browser: Browser, server: Server, role: str) -> Page:
    page = browser.new_page()
    page.goto(f"{server.url}/ui/?plain")
    page.wait_for_selector("body[data-ready]")
    page.fill("#token", TOKENS[role])
    page.click("#sign-in button[type=submit]")
    page.click("#tab-statistics")
    return page


def test_ten_runs_with_two_failures_show_twenty_percent(
    browser: Browser, server: Server, ten_runs: None
) -> None:
    page = open_page(browser, server, "admin")
    row = page.locator(f"[data-testid=statistics-workflow][data-workflow={OWN}]")
    row.wait_for()
    assert row.locator("[data-col=runs]").inner_text() == "10"
    assert row.locator("[data-col=failed]").inner_text() == "2"
    assert row.locator("[data-col=rate]").inner_text() == "20%"
    assert row.locator("[data-col=duration]").inner_text() == "12.5 s"
    assert row.locator("[data-col=versions]").inner_text() == "v1: 5 (0 failed), v2: 5 (2 failed)"


def test_a_workflow_name_with_markup_is_shown_as_text(
    browser: Browser, server: Server, ten_runs: None
) -> None:
    page = open_page(browser, server, "admin")
    page.wait_for_selector("[data-testid=statistics-workflow]")
    assert page.evaluate("window.pwned") is None
    assert TRAP in page.locator("#statistics-rows").inner_text()


def test_an_operator_is_told_that_only_an_admin_may_read_the_page(
    browser: Browser, server: Server, ten_runs: None
) -> None:
    page = open_page(browser, server, "operator")
    page.wait_for_function(
        "!['', 'loading'].includes(document.getElementById('statistics-status').innerText)"
    )
    assert "only an admin" in page.locator("#statistics-status").inner_text()
    assert page.locator("[data-testid=statistics-workflow]").count() == 0
