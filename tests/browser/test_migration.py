"""US-32 in a browser: the table that says which runs can move to another version, and why not."""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser, Page

from tests.browser.conftest import Server
from tests.unit.actions.helpers import write_all_forms
from tests.unit.api.helpers import EXAMPLE, NAME, TOKENS

pytestmark = pytest.mark.browser

OWN = "moving_workflow"  # the server is shared with other modules: a workflow of its own
BASE = EXAMPLE.replace(f"workflow: {NAME}", f"workflow: {OWN}", 1)
CHANGED = BASE.replace("version: 1", "version: 2", 1).replace(
    '    with: {parts: "${check[*].rows}"}\n',
    '    with: {parts: "${check[*].rows}"}\n    ensures: "len(${consolidate.rows}) > 0"\n',
)
SAME = BASE.replace("version: 1", "version: 3", 1)


@pytest.fixture(scope="module")
def three_versions(server: Server) -> None:
    env = server.env
    write_all_forms(env.config.data_dir, "2025Q1")
    for number, text in enumerate(
        (BASE, CHANGED, SAME), start=1
    ):  # a draft is replaced by the next
        saved = env.call("designer", "POST", f"/workflows/{OWN}/versions", json={"text": text})
        assert saved.status_code == 201 and saved.json()["diagnostics"] == [], saved.text
        published = env.call("designer", "POST", f"/workflows/{OWN}/versions/{number}/publish")
        assert published.status_code == 200, published.text
    started = env.call(
        "operator",
        "POST",
        f"/workflows/{OWN}/runs",
        json={"version": 1, "inputs": {"quarter": "2025Q1"}},
    )
    assert started.status_code == 202, started.text


def open_page(browser: Browser, server: Server) -> Page:
    page = browser.new_page()
    page.goto(f"{server.url}/ui/?plain")
    page.wait_for_selector("body[data-ready]")
    page.fill("#token", TOKENS["operator"])
    page.click("#sign-in button[type=submit]")
    return page


def check(page: Page, source: str, target: str) -> None:
    page.click("#tab-migration")
    page.wait_for_function("document.getElementById('migration-workflow').options.length >= 1")
    page.select_option("#migration-workflow", OWN)
    page.wait_for_function("document.getElementById('migration-from').options.length >= 3")
    page.select_option("#migration-from", source)
    page.select_option("#migration-to", target)
    page.click("#migration-check")


def test_a_run_that_can_move_is_marked_so(
    browser: Browser, server: Server, three_versions: None
) -> None:
    page = open_page(browser, server)
    check(page, "1", "3")
    row = page.locator("[data-testid=migration-run]").first
    row.wait_for()
    assert "can move" in row.inner_text() and "WAITING" in row.inner_text()
    assert page.locator("#migration-status").inner_text().endswith("can move")
    page.close()


def test_a_run_that_cannot_move_shows_every_reason_with_its_code_and_step(
    browser: Browser, server: Server, three_versions: None
) -> None:
    page = open_page(browser, server)
    check(page, "1", "2")
    reason = page.locator("[data-testid=migration-run] li[data-code=E-MIG-004]")
    reason.wait_for()
    assert "consolidate" in reason.inner_text() and "ensures" in reason.inner_text()
    assert "cannot move" in page.locator("[data-testid=migration-run]").first.inner_text()
    page.close()


def test_a_run_is_moved_from_the_page_and_is_then_on_the_new_version(
    browser: Browser, server: Server, three_versions: None
) -> None:
    page = open_page(browser, server)
    check(page, "1", "3")
    page.locator("[data-testid=migration-run] [data-testid=move]").first.click()
    page.wait_for_function(
        "document.getElementById('migration-status').textContent.includes('moved to v3')"
    )
    page.wait_for_function("document.querySelectorAll('[data-testid=migration-run]').length === 0")
    runs = server.env.call("operator", "GET", "/runs").json()
    assert [r["version"] for r in runs if r["workflow"] == OWN] == [3]
    page.close()
