"""US-52 in a browser: the home page is a desk of the role, and the page writes times and run
numbers the way a person does."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Browser, ConsoleMessage, Page

from tests.browser.conftest import Server, use_token
from tests.unit.actions.helpers import write_all_forms
from tests.unit.api.helpers import EXAMPLE, NAME, TOKENS

pytestmark = pytest.mark.browser


def open_home(browser: Browser, server: Server, role: str) -> tuple[Page, list[str]]:
    """Sign in as ``role`` and open the home page; the second value is what the console says."""
    page = browser.new_page()
    problems: list[str] = []
    page.on("pageerror", lambda error: problems.append(str(error)))

    def heard(message: ConsoleMessage) -> None:
        if message.type in ("error", "warning"):
            problems.append(message.text)

    page.on("console", heard)
    page.goto(f"{server.url}/ui/?plain")
    page.wait_for_selector("body[data-ready]")
    use_token(page, TOKENS[role], "home")
    page.wait_for_function("document.getElementById('count-runs').textContent !== '\u2013'")
    return page, problems


def publish(server: Server) -> None:
    server.env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    server.env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish")


def start(server: Server) -> str:
    write_all_forms(server.env.config.data_dir, "2025Q1")
    reply = server.env.call(
        "designer", "POST", f"/workflows/{NAME}/runs", json={"inputs": {"quarter": "2025Q1"}}
    )
    assert reply.status_code == 202, reply.text
    run_id: str = reply.json()["run_id"]
    return run_id


def test_the_designer_sees_the_workflows_with_their_state(
    browser: Browser, forms_server: Server
) -> None:
    publish(forms_server)
    page, problems = open_home(browser, forms_server, "designer")
    row = page.locator(f"[data-testid=workflow][data-name={NAME}]")
    row.wait_for()
    assert "published" in row.inner_text() and "v1" in row.inner_text()
    assert page.is_visible("#home-actions >> text=Load the example")
    assert page.text_content("#home-main-title") == "Your workflows"
    assert problems == []
    page.close()


def test_a_designer_without_a_workflow_is_led_to_the_example(
    browser: Browser, forms_server: Server
) -> None:
    page, _ = open_home(browser, forms_server, "designer")
    page.locator("#home-main-body >> text=Load the example").click()
    page.wait_for_function("document.body.dataset.view === 'editor'")
    page.close()


def test_the_operator_sees_how_many_regions_have_sent_their_forms(
    browser: Browser, forms_server: Server
) -> None:
    page, problems = open_home(browser, forms_server, "clerk")
    progress = page.locator("[data-testid=forms-progress]")
    progress.wait_for()
    assert progress.inner_text() == "0 of 9 regions sent"
    assert page.is_visible("#home-main-body >> text=Send the forms")
    assert problems == []
    page.close()

    write_all_forms(forms_server.env.config.data_dir, "2025Q1")
    page, _ = open_home(browser, forms_server, "clerk")
    page.locator("[data-testid=forms-progress]").wait_for()
    assert page.inner_text("[data-testid=forms-progress]") == "9 of 9 regions sent"
    page.close()


def test_the_chief_statistician_approves_from_the_list(
    browser: Browser, forms_server: Server
) -> None:
    publish(forms_server)
    run_id = start(forms_server)
    page, problems = open_home(browser, forms_server, "operator")
    item = page.locator(f"[data-testid=waiting][data-run={run_id}]")
    item.wait_for()
    assert page.inner_text("#count-waiting") == "1"
    item.locator("[data-testid=approve]").click()
    page.wait_for_selector("#home-main-note:has-text('approved')")
    page.wait_for_function("document.getElementById('count-waiting').textContent === '0'")
    report = forms_server.env.call("admin", "GET", f"/runs/{run_id}").json()
    assert report["outcome"] == "SUCCEEDED"
    assert problems == []
    page.close()


def test_the_admin_sees_the_week_and_the_recent_runs_with_short_numbers(
    browser: Browser, forms_server: Server
) -> None:
    publish(forms_server)
    run_id = start(forms_server)
    page, problems = open_home(browser, forms_server, "admin")
    row = page.locator(f"[data-testid=recent][data-run={run_id}]")
    row.wait_for()
    assert page.inner_text("#count-runs") == "1"
    link = row.locator("a")
    assert link.inner_text() == run_id[:8]
    assert link.get_attribute("title") == run_id
    assert re.search(r"8 Nov, \d\d:\d\d", row.inner_text())  # the clock of the test core is fixed
    assert page.locator("#view-home p:visible").count() <= 3  # no paragraph without data
    assert problems == []
    page.close()


def test_the_title_of_the_tab_names_the_page(browser: Browser, forms_server: Server) -> None:
    page, _ = open_home(browser, forms_server, "admin")
    assert page.title() == "Home · FlowStat"
    page.click("#tab-runs")
    page.wait_for_function("document.body.dataset.view === 'runs'")
    assert page.title() == "Runs · FlowStat"
    page.close()


# -- format.js: pure functions, so they are called with a clock that is given ---------------------
def format_call(page: Page, expression: str) -> object:
    return page.evaluate(f"import('/ui/format.js').then((f) => {expression})")


@pytest.fixture
def formatting(browser: Browser, forms_server: Server) -> Page:
    page = browser.new_page()
    page.goto(f"{forms_server.url}/ui/?plain")
    page.wait_for_selector("body[data-ready]")
    return page


def test_a_time_is_written_as_a_person_says_it(formatting: Page) -> None:
    now = "Date.parse('2026-11-18T12:00:20Z')"
    assert format_call(formatting, f"f.when('2026-11-18T12:00:00Z', {now})") == "just now"
    assert format_call(formatting, f"f.when('2026-11-18T11:55:00Z', {now})") == "5 min ago"
    assert format_call(formatting, f"f.when('2026-11-18T10:00:00Z', {now})") == "2 h ago"
    older = str(format_call(formatting, f"f.when('2026-11-10T12:00:00Z', {now})"))
    assert re.fullmatch(r"\d{1,2} Nov, \d\d:\d\d", older), older
    other_year = str(format_call(formatting, f"f.when('2025-03-04T12:00:00Z', {now})"))
    assert re.fullmatch(r"\d{1,2} Mar 2025, \d\d:\d\d", other_year), other_year
    assert format_call(formatting, f"f.when('', {now})") == ""
    assert format_call(formatting, f"f.when('not a time', {now})") == "not a time"
    formatting.close()


def test_durations_numbers_and_greetings(formatting: Page) -> None:
    assert format_call(formatting, "f.duration(8.43)") == "8.4 s"
    assert format_call(formatting, "f.duration(840)") == "14 min"
    assert format_call(formatting, "f.duration(7500)") == "2 h 5 min"
    assert format_call(formatting, "f.duration(null)") == "\u2013"
    assert format_call(formatting, "f.shortId('ffe9a9fe79cc')") == "ffe9a9fe"
    assert format_call(formatting, "f.plural(1, 'run') + ', ' + f.plural(3, 'run')") == (
        "1 run, 3 runs"
    )
    hours = {3: "Good night", 9: "Good morning", 14: "Good afternoon", 20: "Good evening"}
    for hour, word in hours.items():
        assert format_call(formatting, f"f.greeting(new Date(2026, 10, 18, {hour}))") == word
    formatting.close()
