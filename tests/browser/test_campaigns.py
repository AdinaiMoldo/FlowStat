"""US-53 in a browser: the page Campaigns, from an empty list to a campaign that collects."""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser, ConsoleMessage, Page

from tests.browser.conftest import Server, use_token
from tests.unit.actions.helpers import write_all_forms
from tests.unit.api.helpers import EXAMPLE, NAME, TOKENS

pytestmark = pytest.mark.browser


def open_page(browser: Browser, server: Server, role: str) -> tuple[Page, list[str]]:
    page = browser.new_page()
    problems: list[str] = []
    page.on("pageerror", lambda error: problems.append(str(error)))

    def heard(message: ConsoleMessage) -> None:
        if message.type in ("error", "warning"):
            problems.append(message.text)

    page.on("console", heard)
    page.goto(f"{server.url}/ui/?plain")
    page.wait_for_selector("body[data-ready]")
    use_token(page, TOKENS[role], "campaigns")
    page.wait_for_function("document.getElementById('campaigns-status').textContent !== undefined")
    return page, problems


def save_workflow(server: Server, published: bool) -> None:
    server.env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    if published:
        server.env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish")


def make_through_the_form(page: Page, name: str = "Quarterly Regional Statistics, Q3 2026") -> None:
    page.click("#campaigns-new")
    page.wait_for_selector("#campaign-regions input")
    page.fill("#campaign-name", name)
    page.fill("#campaign-year", "2026")
    page.select_option("#campaign-quarter", "3")
    page.fill("#campaign-deadline", "2026-11-30")
    page.click("#campaign-save")
    page.wait_for_selector("[data-testid=campaign]")


def test_the_list_says_there_are_no_campaigns_and_who_makes_them(
    browser: Browser, forms_server: Server
) -> None:
    admin, problems = open_page(browser, forms_server, "admin")
    admin.wait_for_selector("#campaigns-empty:not([hidden])")
    assert "No campaigns yet" in admin.inner_text("#campaigns-empty")
    assert "New campaign" in admin.inner_text("#campaigns-empty")
    assert admin.is_visible("#campaigns-new")
    assert problems == []
    admin.close()
    for role in ("designer", "operator"):
        page, problems = open_page(browser, forms_server, role)
        page.wait_for_selector("#campaigns-empty:not([hidden])")
        assert page.is_visible("#tab-campaigns")
        assert page.is_hidden("#campaigns-new")
        assert "An administrator makes the campaigns" in page.inner_text("#campaigns-empty")
        assert problems == []
        page.close()


def test_an_administrator_makes_a_draft_and_the_page_shows_nothing_has_come(
    browser: Browser, forms_server: Server
) -> None:
    save_workflow(forms_server, published=True)
    page, problems = open_page(browser, forms_server, "admin")
    make_through_the_form(page)
    row = page.locator("[data-testid=campaign]")
    assert "draft" in row.inner_text() and "2026Q3" in row.inner_text()
    assert "30 Nov" in row.inner_text()  # with the year when it is not this one
    page.wait_for_function("location.hash.startsWith('#/campaigns/')")
    assert page.inner_text("#campaign-counts") == "0 of 9 received, 0 valid, 9 missing"
    assert "A preliminary result needs 7 valid forms (0 now)" in page.inner_text("#campaign-ready")
    assert page.locator("[data-testid=campaign-region]").count() == 9
    assert page.title() == "Campaigns · FlowStat"
    assert problems == []
    page.close()


def test_the_page_offers_the_changes_the_server_allows_and_follows_the_forms(
    browser: Browser, forms_server: Server
) -> None:
    save_workflow(forms_server, published=True)
    page, problems = open_page(browser, forms_server, "admin")
    make_through_the_form(page)
    moves = page.locator("[data-testid=move]")
    assert [m.get_attribute("data-to") for m in moves.all()] == ["OPEN", "ARCHIVED"]
    page.click("[data-testid=move][data-to=OPEN]")
    page.wait_for_selector("[data-testid=move][data-to=COLLECTING]")
    assert "Start collecting" in page.inner_text("[data-testid=move][data-to=COLLECTING]")
    assert page.is_hidden("[data-testid=edit]")  # only a draft can be edited
    page.click("[data-testid=move][data-to=COLLECTING]")
    page.wait_for_selector("[data-testid=move][data-to=VALIDATING]")

    write_all_forms(forms_server.env.config.data_dir, "2026Q3", missing=("JAL", "ISK"))
    page.reload()
    page.wait_for_selector("[data-testid=campaign-region]")
    assert page.inner_text("#campaign-counts") == "7 of 9 received, 7 valid, 2 missing"
    assert "Enough for a preliminary result" in page.inner_text("#campaign-ready")
    assert (
        page.locator("[data-testid=campaign-region][data-region=JAL]")
        .inner_text()
        .endswith("missing")
    )
    assert (
        page.locator("[data-testid=campaign-region][data-region=BIS]")
        .inner_text()
        .endswith("valid")
    )
    assert "7 of 9" in page.inner_text("[data-testid=campaign]")
    assert problems == []
    page.close()


def test_a_campaign_whose_workflow_is_not_published_cannot_open_and_says_why(
    browser: Browser, forms_server: Server
) -> None:
    save_workflow(forms_server, published=False)
    page, _ = open_page(browser, forms_server, "admin")
    make_through_the_form(page)
    page.click("[data-testid=move][data-to=OPEN]")
    page.wait_for_selector("#campaign-note:not([hidden])")
    assert "publish" in page.inner_text("#campaign-note")
    assert page.locator("[data-testid=move][data-to=OPEN]").count() == 1  # still a draft
    page.close()


def test_a_draft_is_edited_and_cancelling_asks_twice(
    browser: Browser, forms_server: Server
) -> None:
    save_workflow(forms_server, published=True)
    page, _ = open_page(browser, forms_server, "admin")
    make_through_the_form(page)
    page.click("[data-testid=edit]")
    page.wait_for_selector("#campaign-regions input")
    assert page.input_value("#campaign-name") == "Quarterly Regional Statistics, Q3 2026"
    page.fill("#campaign-deadline", "2026-12-15")
    page.click("#campaign-save")
    page.wait_for_function("document.getElementById('campaign-form').hidden")
    page.wait_for_function(
        "document.getElementById('campaign-deadline-line').textContent.includes('15 Dec')"
    )
    page.click("[data-testid=move][data-to=ARCHIVED]")
    assert "Press again" in page.inner_text("[data-testid=move][data-to=ARCHIVED]")
    assert "archived" not in page.inner_text("[data-testid=campaign]")  # nothing happened yet
    page.click("[data-testid=move][data-to=ARCHIVED]")
    page.wait_for_function(
        "document.querySelector('[data-testid=campaign]').textContent.includes('archived')"
    )
    assert page.locator("[data-testid=move]").count() == 0
    page.close()


def test_a_form_with_no_region_is_refused_in_words(browser: Browser, forms_server: Server) -> None:
    save_workflow(forms_server, published=True)
    page, _ = open_page(browser, forms_server, "admin")
    page.click("#campaigns-new")
    page.wait_for_selector("#campaign-regions input")
    for box in page.locator("#campaign-regions input").all():
        box.uncheck()
    page.click("#campaign-save")
    page.wait_for_selector("#campaign-form-note:not([hidden])")
    assert "at least one region" in page.inner_text("#campaign-form-note")
    assert page.is_visible("#campaign-form")  # what was typed is still there
    page.close()


def test_an_operator_sees_the_state_but_has_no_buttons(
    browser: Browser, forms_server: Server
) -> None:
    save_workflow(forms_server, published=True)
    forms_server.env.call(
        "admin",
        "POST",
        "/campaigns",
        json={
            "name": "Q3",
            "year": 2026,
            "quarter": 3,
            "regions": ["BIS", "OSC", "BAT"],
            "deadline": "2026-11-30",
            "workflow": NAME,
            "workflow_version": 1,
        },
    )
    page, problems = open_page(browser, forms_server, "operator")
    page.click("[data-testid=campaign]")
    page.wait_for_selector("[data-testid=campaign-region]")
    assert page.locator("[data-testid=campaign-region]").count() == 3
    assert page.is_hidden("#campaign-actions")
    assert page.is_hidden("#campaigns-new")
    assert problems == []
    page.close()
