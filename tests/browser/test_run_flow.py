"""US-44 in a browser: the example, a run started from the page, the answer to an approval, and the
graph of the run coloured by the states of its steps."""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser, Page

from tests.browser.conftest import Server
from tests.browser.test_editor import open_page, set_text
from tests.unit.actions.helpers import write_all_forms
from tests.unit.api.helpers import EXAMPLE, TOKENS

pytestmark = pytest.mark.browser

RENAMED = EXAMPLE.replace("workflow: quarterly_consolidation", "workflow: demo_from_the_page")


def editor_text(page: Page) -> str:
    text: str = page.evaluate("monaco.editor.getEditors()[0].getModel().getValue()")
    return text


def sign_in(page: Page, role: str) -> None:
    page.click("#forget")
    page.fill("#token", TOKENS[role])
    page.click("#sign-in button[type=submit]")


def classes(page: Page) -> dict[str, str]:
    found: dict[str, str] = page.evaluate(
        "Object.fromEntries([...document.getElementById('run-graph').cy.nodes()]"
        ".map(n => [n.id(), n.classes().join(' ')]))"
    )
    return found


def test_the_example_button_puts_the_example_in_the_editor(
    browser: Browser, server: Server, need_editor: None
) -> None:
    page = open_page(browser, server, "designer")
    page.click("#example")
    page.wait_for_function("monaco.editor.getEditors()[0].getModel().getValue().length > 100")
    assert editor_text(page) == EXAMPLE
    assert page.is_disabled("#run")  # nothing is published yet
    page.close()


def test_a_run_is_started_and_answered_on_the_page(
    browser: Browser, server: Server, need_editor: None
) -> None:
    write_all_forms(server.env.config.data_dir, "2025Q1")
    page = open_page(browser, server, "designer")
    set_text(page, RENAMED)
    page.click("#save")
    page.wait_for_function("document.getElementById('state').textContent.includes('draft')")
    page.click("#publish")
    page.wait_for_function("document.getElementById('state').textContent.includes('published')")

    # Run: the form starts from the inputs the text declares
    page.click("#run")
    assert page.input_value("#run-inputs") == '{"quarter":"2025Q1"}'
    page.click("#run-start")
    page.wait_for_selector("[data-testid=ask]", timeout=20000)  # the run stops at the approval
    assert page.inner_text("#run-summary").endswith("WAITING")
    assert page.input_value("#token") == ""

    # the graph is coloured: what is done is green, the approval waits, the rest has no colour
    page.wait_for_function("document.getElementById('run-graph').cy !== undefined")
    page.wait_for_function(
        "document.getElementById('run-graph').cy.getElementById('approve').hasClass('st-wait')"
    )
    waiting = classes(page)
    assert "st-ok" in waiting["consolidate"]
    assert "st-wait" in waiting["approve"]
    assert "st-" not in waiting["publish"]

    # a designer holds no title: the core refuses, and the page says why
    page.click("[data-testid=ask] button.approve")
    page.wait_for_selector("#run-note:not([hidden])")
    assert "may not answer this approval" in page.inner_text("#run-note")
    assert page.is_visible("[data-testid=ask]")

    # the chief statistician answers
    sign_in(page, "operator")
    page.click("#tab-runs")
    page.click("[data-testid=run]")
    page.wait_for_selector("[data-testid=ask] button.approve")
    page.click("[data-testid=ask] button.approve")
    page.wait_for_function(
        "document.getElementById('run-summary').textContent.endsWith('SUCCEEDED')", timeout=20000
    )
    page.wait_for_function(
        "document.getElementById('run-graph').cy.getElementById('publish').hasClass('st-ok')"
    )
    finished = classes(page)
    assert "st-ok" in finished["publish"]
    assert "st-skip" in finished["return_to_regions"]
    assert page.is_hidden("#run-decision")
    page.close()


def test_a_rejection_colours_the_other_branch(
    browser: Browser, server: Server, need_editor: None
) -> None:
    write_all_forms(server.env.config.data_dir, "2025Q1")
    name = "rejected_from_the_page"
    server.env.call(
        "designer", "POST", f"/workflows/{name}/versions",
        json={"text": EXAMPLE.replace("workflow: quarterly_consolidation", f"workflow: {name}")},
    )  # fmt: skip
    server.env.call("designer", "POST", f"/workflows/{name}/versions/1/publish")
    page = open_page(browser, server, "operator")
    page.click("#tab-runs")
    started = server.env.call(
        "operator", "POST", f"/workflows/{name}/runs", json={"inputs": {"quarter": "2025Q1"}}
    )
    assert started.status_code == 202, started.text
    page.click("#runs-refresh")
    page.click(f"[data-testid=run][data-id='{started.json()['run_id']}']")
    page.wait_for_selector("[data-testid=ask] button.reject", timeout=20000)
    page.click("[data-testid=ask] button.reject")
    page.wait_for_function(
        "document.getElementById('run-graph').cy.getElementById('return_to_regions')"
        ".hasClass('st-ok')",
        timeout=20000,
    )
    result = classes(page)
    assert "st-skip" in result["publish"]
    page.close()


def test_a_run_that_is_not_published_cannot_be_started(
    browser: Browser, server: Server, need_editor: None
) -> None:
    page = open_page(browser, server, "designer")
    set_text(page, RENAMED.replace("demo_from_the_page", "never_published"))
    page.click("#save")
    page.wait_for_function("document.getElementById('state').textContent.includes('draft')")
    assert page.is_disabled("#run")
    assert page.get_attribute("#run", "title") == "Publish this version first"
    page.close()


def test_the_colours_of_the_states(browser: Browser, server: Server, need_editor: None) -> None:
    """A fan-out that is collapsed shows its worst state; none shows no colour."""
    page = open_page(browser, server, "designer")
    answer = page.evaluate(
        """async () => {
          const g = await import('/ui/graph.js');
          return {
            one: ['SUCCEEDED', 'WAITING', 'FAILED', 'SKIPPED', 'PENDING'].map(g.stateClass),
            groups: [
              g.groupClass(['st-ok', 'st-ok']),
              g.groupClass(['st-ok', 'st-bad', 'st-wait']),
              g.groupClass(['st-ok', 'st-wait']),
              g.groupClass(['st-skip', 'st-skip']),
              g.groupClass(['st-ok', '']),
              g.groupClass(['', '']),
            ],
          };
        }"""
    )
    assert answer["one"] == ["st-ok", "st-wait", "st-bad", "st-skip", ""]
    assert answer["groups"] == ["st-ok", "st-bad", "st-wait", "st-skip", "", ""]
    page.close()
