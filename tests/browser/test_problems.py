"""US-56 in a browser: the page Problems, from a form with an error to a comment and a new owner."""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Browser, ConsoleMessage, Page

from tests.browser.conftest import Server, use_token
from tests.unit.actions.helpers import FIVE_ROWS, Row, write_form
from tests.unit.api.helpers import TOKENS, headers

pytestmark = pytest.mark.browser

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
BAD: list[Row] = [*FIVE_ROWS[:3], ("I04", "Deaths", -2.5), FIVE_ROWS[4]]


def send(server: Server, tmp_path: Path, rows: list[Row]) -> None:
    content = write_form(tmp_path / "made.xlsx", rows).read_bytes()
    reply = server.env.client.put(
        "/forms/BAT/2025Q1", content=content, headers={**headers("operator"), "Content-Type": XLSX}
    )
    assert reply.status_code == 200, reply.text


def open_page(browser: Browser, server: Server, role: str) -> tuple[Page, list[str]]:
    page = browser.new_page()
    heard: list[str] = []
    page.on("pageerror", lambda error: heard.append(str(error)))

    def listen(message: ConsoleMessage) -> None:
        if message.type in ("error", "warning"):
            heard.append(message.text)

    page.on("console", listen)
    page.goto(f"{server.url}/ui/?plain")
    page.wait_for_selector("body[data-ready]")
    use_token(page, TOKENS[role], "problems")
    return page, heard


def test_an_error_of_a_form_is_a_problem_that_an_operator_takes_and_comments(
    browser: Browser, forms_server: Server, tmp_path: Path
) -> None:
    send(forms_server, tmp_path, BAD)
    page, heard = open_page(browser, forms_server, "operator")
    page.wait_for_selector("[data-testid=problem]")
    row = page.locator("[data-testid=problem]")
    assert row.count() == 1
    assert "open" in row.inner_text() and "VAL-RANGE-001" in row.inner_text()
    assert "BAT 2025Q1" in row.inner_text() and "olga" in row.inner_text()
    row.click()
    page.wait_for_selector("[data-testid=problem-event]")
    assert page.inner_text("[data-testid=problem-assignee]") == "olga"
    assert "opened by system" in page.inner_text("#problem-events")
    page.fill("#problem-comment-text", "  I will ask the region  ")
    page.click("#problem-comment")
    page.wait_for_function("document.querySelectorAll('[data-testid=problem-event]').length === 2")
    assert "commented by olga" in page.inner_text("#problem-events")
    assert "I will ask the region" in page.inner_text("#problem-events")
    page.fill("#problem-assign-input", "nobody-here")
    page.click("#problem-assign")
    page.wait_for_selector("#problem-note:not([hidden])")
    page.fill("#problem-assign-input", "kira")
    page.click("#problem-assign")
    page.wait_for_function("document.getElementById('problem-assignee').textContent === 'kira'")
    page.click("#problem-assign-me")
    page.wait_for_function("document.getElementById('problem-assignee').textContent === 'olga'")
    assert [m for m in heard if "422" not in m] == []  # the refused name is a 422 in the console
    page.close()


def test_a_corrected_form_resolves_the_problem_and_a_designer_may_only_read(
    browser: Browser, forms_server: Server, tmp_path: Path
) -> None:
    send(forms_server, tmp_path, BAD)
    send(forms_server, tmp_path, FIVE_ROWS)
    page, heard = open_page(browser, forms_server, "designer")
    page.wait_for_selector("#problems-empty:not([hidden])")  # the default list is of the active
    page.uncheck("#problems-active")
    page.wait_for_selector("[data-testid=problem][data-status=RESOLVED]")
    page.click("[data-testid=problem]")
    page.wait_for_selector("[data-testid=problem-event]")
    kinds = page.eval_on_selector_all(
        "[data-testid=problem-event]", "nodes => nodes.map(n => n.dataset.kind)"
    )
    assert kinds == ["OPENED", "REVALIDATING", "RESOLVED"]
    assert page.is_hidden("#problem-write")
    assert [m for m in heard if "403" in m or "401" in m] == []
    assert heard == []
    page.close()
