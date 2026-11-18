"""US-51 in a browser: the whole demonstration with the roles of the office, page by page.

A designer publishes the workflow, an operator sends the forms and starts the run, the chief
statistician answers the approval; then a set with a mistake stops at its check and a set of six
regions does not reach the goal. Nothing may reach the console of the browser on the way (no error,
no warning, no refused request), and the pages that have nothing to list say what is missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

from tests.browser.conftest import Server, sign_out, use_token
from tests.unit.api.helpers import TOKENS

pytestmark = pytest.mark.browser


def watch(page: Page, problems: list[str]) -> Page:
    """Collect what the page puts into the console, the errors of the page, and refused requests."""
    page.on(
        "console",
        lambda m: (
            problems.append(f"console {m.type}: {m.text}")
            if m.type in ("error", "warning")
            else None
        ),
    )
    page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
    page.on(
        "response",
        lambda r: problems.append(f"{r.status} {r.url}") if r.status >= 400 else None,
    )
    return page


def open_as(browser: Browser, server: Server, role: str, view: str, problems: list[str]) -> Page:
    page = watch(browser.new_page(viewport={"width": 1280, "height": 800}), problems)
    page.goto(f"{server.url}/ui/")
    page.wait_for_selector("body[data-ready]")
    use_token(page, TOKENS[role], view)
    return page


def go(page: Page, view: str) -> None:
    page.click(f"#tab-{view}")
    page.wait_for_function("view => document.body.dataset.view === view", arg=view)


def send(page: Page, folder: Path) -> None:
    if page.evaluate("document.body.dataset.view") != "forms":
        go(page, "forms")
    page.evaluate("document.getElementById('forms-status').textContent = ''")  # the last answer
    page.set_input_files("#forms-files", sorted(str(p) for p in folder.glob("*.xlsx")))
    page.wait_for_function(
        "document.getElementById('forms-status').textContent.includes('form(s) sent')"
    )


def start_run(page: Page) -> None:
    go(page, "editor")
    page.click("#workflows li >> nth=0")
    page.wait_for_function("!document.getElementById('run').disabled")
    page.click("#run")
    page.click("#run-start")


def open_latest_run(page: Page) -> None:
    go(page, "runs")
    page.click("#runs-rows tr >> nth=0")
    page.wait_for_selector("#run-steps tr")


def test_the_demonstration_runs_through_every_role_without_a_problem(
    browser: Browser, forms_server: Server, sets: dict[str, Path], need_editor: None
) -> None:
    server = forms_server
    problems: list[str] = []

    # the designer writes and publishes; the page of the forms is for reading only
    designer = open_as(browser, server, "designer", "editor", problems)
    designer.click("#example")
    designer.wait_for_function("monaco.editor.getEditors()[0].getModel().getValue().length > 100")
    designer.click("#save")
    designer.wait_for_function("document.getElementById('state').textContent.includes('draft')")
    designer.click("#publish")
    designer.wait_for_function("document.getElementById('state').textContent.includes('published')")
    go(designer, "forms")
    assert designer.is_hidden("#forms-send")
    assert "sent by the operators" in designer.inner_text("#forms-readonly")
    designer.close()

    # the operator sends nine good forms: green note, every form passes, the run waits for approval
    operator = open_as(browser, server, "operator", "forms", problems)
    send(operator, sets["good"])
    assert operator.locator("[data-testid=rules] .badge.good").count() == 9
    assert operator.eval_on_selector("#forms-note", "n => n.classList.contains('good')")
    start_run(operator)
    operator.wait_for_selector("[data-testid=ask]", timeout=20000)
    assert operator.is_disabled("#save")  # an operator runs workflows and does not change them
    assert operator.is_hidden("#example")

    # the chief statistician sees it on the home page and answers
    chief = open_as(browser, server, "operator", "home", problems)
    chief.click("[data-testid=waiting] a")
    chief.wait_for_selector("[data-testid=ask] button.approve")
    chief.click("[data-testid=ask] button.approve")
    chief.wait_for_function(
        "document.getElementById('run-summary').textContent.includes('SUCCEEDED')", timeout=20000
    )
    assert chief.is_hidden("[data-testid=failure]")
    chief.close()  # it holds the token of the operator too: signing out below ends that session

    # a form with a mistake: the run stops at the check of that form, and the page says where
    send(operator, sets["with-a-mistake"])
    assert "BAT breaks the rules" in operator.inner_text("#forms-note")
    start_run(operator)
    open_latest_run(operator)
    operator.wait_for_function(
        "document.getElementById('run-summary').textContent.includes('FAILED')", timeout=20000
    )
    banner = operator.inner_text("[data-testid=failure]")
    assert "The run stopped at check[BAT]" in banner
    assert "E-ACT-005" in banner and "value is negative" in banner
    failed_row = operator.locator("[data-testid=step].failed")
    assert failed_row.count() == 1 and "check[BAT]" in failed_row.inner_text()
    cancelled = operator.locator("[data-testid=step] .badge.off")
    assert cancelled.count() > 0  # the steps that did not run are marked, not left bare

    # six regions: the goal is not reached, and the page says which steps failed
    go(operator, "forms")
    operator.click("#forms-clear")
    operator.wait_for_function("document.getElementById('forms-clear').hidden")
    send(operator, sets["six-regions"])
    assert "6 came" in operator.inner_text("#forms-note")
    start_run(operator)
    open_latest_run(operator)
    operator.wait_for_function(
        "document.getElementById('run-summary').textContent.includes('GOAL_NOT_REACHED')",
        timeout=20000,
    )
    assert "The goal was not reached" in operator.inner_text("[data-testid=failure]")

    # the pages of the admin: totals where there are runs, an explanation where there is nothing
    sign_out(operator)
    admin = open_as(browser, server, "admin", "statistics", problems)
    assert admin.inner_text("#statistics-tiles .stat >> nth=0").startswith("3")
    go(admin, "services")
    assert "No services are connected" in admin.inner_text("#services-empty")
    go(admin, "migration")
    assert "Only one version" in admin.inner_text("#migration-empty")
    assert admin.is_disabled("#migration-check")

    assert problems == []


def test_signing_out_while_a_page_refreshes_leaves_nothing_in_the_console(
    browser: Browser, server: Server
) -> None:
    for _ in range(5):  # a refresh that is on its way when the session ends is a matter of timing
        problems: list[str] = []
        page = watch(browser.new_page(), problems)
        page.goto(f"{server.url}/ui/?live=10")  # the runs are asked for a hundred times a second
        page.wait_for_selector("body[data-ready]")
        use_token(page, TOKENS["clerk"], "runs")
        page.wait_for_timeout(300)
        sign_out(page)
        page.wait_for_timeout(600)
        assert problems == []
        page.close()
