"""US-26 in a browser: the page, the pause of 500 ms and the markers."""

from __future__ import annotations

import time

import pytest
from playwright.sync_api import Browser, Page

from tests.browser.conftest import Server
from tests.unit.api.helpers import TOKENS

pytestmark = pytest.mark.browser

GOOD = """workflow: new_workflow
version: 1
inputs:
  quarter: {type: string}
steps:
  approve:
    action: approval.wait
    with: {role: chief_statistician}
goals: {all_of: [approve]}
"""
# the goal names a step that does not exist
BAD = GOOD.replace("[approve]", "[approv]")


def open_page(browser: Browser, server: Server, role: str, query: str = "") -> Page:
    page = browser.new_page()
    page.goto(f"{server.url}/ui/{query}")
    page.wait_for_selector("body[data-ready]")
    page.fill("#token", TOKENS[role])
    page.click("#sign-in button[type=submit]")
    return page


def set_text(page: Page, text: str) -> None:
    page.evaluate(
        "text => monaco.editor.getEditors()[0].getModel().setValue(text)",
        text,
    )


def markers(page: Page) -> list[dict[str, object]]:
    found: list[dict[str, object]] = page.evaluate(
        "monaco.editor.getModelMarkers({}).map(m => ({line: m.startLineNumber,"
        " column: m.startColumn, message: m.message, severity: m.severity}))"
    )
    return found


def test_the_editor_is_loaded(browser: Browser, server: Server, need_editor: None) -> None:
    page = open_page(browser, server, "designer")
    assert page.get_attribute("body", "data-ready") == "monaco"
    assert page.locator(".monaco-editor").count() == 1
    page.close()


def test_an_error_gets_a_marker_at_its_position_after_a_pause_of_500_ms(
    browser: Browser, server: Server, need_editor: None
) -> None:
    """US-26: an error is typed, the person pauses 500 ms, a marker appears at its position."""
    expected = server.env.call("designer", "POST", "/validate", {"text": BAD}).json()["diagnostics"]
    assert expected, "the text must have an error for this test to mean anything"
    page = open_page(browser, server, "designer")
    sent: list[float] = []
    page.on(
        "request", lambda r: sent.append(time.monotonic()) if r.url.endswith("/validate") else None
    )

    set_text(page, GOOD)
    page.wait_for_function("document.getElementById('status').textContent === 'no problems'")
    assert markers(page) == []
    sent.clear()

    # two edits 200 ms apart count as one burst: one check, and only after the last of them
    set_text(page, BAD + "\n")
    page.wait_for_timeout(200)
    set_text(page, BAD)
    typed = time.monotonic()
    page.wait_for_function("monaco.editor.getModelMarkers({}).length > 0", timeout=5000)

    assert len(sent) == 1
    assert sent[0] - typed >= 0.45
    found = markers(page)
    assert (found[0]["line"], found[0]["column"]) == (expected[0]["line"], expected[0]["column"])
    assert expected[0]["code"] in str(found[0]["message"])
    assert page.locator("[data-testid=problem]").count() == len(expected)
    page.close()


def test_fixing_the_error_removes_the_marker(
    browser: Browser, server: Server, need_editor: None
) -> None:
    page = open_page(browser, server, "designer")
    set_text(page, BAD)
    page.wait_for_function("monaco.editor.getModelMarkers({}).length > 0", timeout=5000)
    set_text(page, GOOD)
    page.wait_for_function("monaco.editor.getModelMarkers({}).length === 0", timeout=5000)
    assert page.locator("[data-testid=problem]").count() == 0
    page.close()


def test_a_new_workflow_is_saved_and_published(
    browser: Browser, server: Server, need_editor: None
) -> None:
    page = open_page(browser, server, "designer")
    page.click("#new")
    page.wait_for_function("document.getElementById('status').textContent === 'no problems'")
    page.click("#save")
    page.wait_for_function("document.getElementById('status').textContent === 'saved as v1'")
    page.click("#publish")
    page.wait_for_function("document.getElementById('status').textContent === 'v1 is published'")
    assert "new_workflow" in page.inner_text("#workflows")
    assert page.inner_text("#state") == "v1 published"
    page.close()


def test_an_operator_is_told_that_the_token_may_not_edit(
    browser: Browser, server: Server, need_editor: None
) -> None:
    page = open_page(browser, server, "operator")
    set_text(page, BAD)
    page.wait_for_function("document.getElementById('status').textContent.includes('may not edit')")
    page.close()


def test_without_the_editor_the_page_is_a_text_area_with_the_same_list(
    browser: Browser, server: Server
) -> None:
    page = open_page(browser, server, "designer", "?plain")
    assert page.get_attribute("body", "data-ready") == "plain"
    assert page.is_visible("#plain")
    page.fill("#plain", BAD)
    page.wait_for_selector("[data-testid=problem]", timeout=5000)
    assert page.locator("[data-testid=problem]").count() >= 1
    page.close()


def test_the_page_shows_where_to_get_the_editor_when_it_is_missing(
    browser: Browser, server: Server
) -> None:
    """The fetched files are made unreachable for the page, as on a machine that did not fetch."""
    page = browser.new_page()
    page.route("**/vendor/**", lambda route: route.abort())
    page.goto(f"{server.url}/ui/")
    page.wait_for_selector("body[data-ready=plain]")
    assert "scripts/fetch_ui.py" in page.inner_text("#notice")
    page.fill("#token", TOKENS["designer"])
    page.click("#sign-in button[type=submit]")
    page.fill("#plain", BAD)
    page.wait_for_selector("[data-testid=problem]", timeout=5000)
    page.close()
