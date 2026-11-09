"""US-27 in a browser: inside ``${`` the list holds what is available at the cursor."""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser, Page

from tests.browser.conftest import Server
from tests.browser.test_editor import open_page
from tests.unit.api.helpers import EXAMPLE

pytestmark = pytest.mark.browser

MARK = "§"


def prepare(page: Page, text: str) -> None:
    """Put the text in the editor and the cursor where the mark is."""
    page.evaluate(
        """text => {
          const editor = monaco.editor.getEditors()[0];
          const model = editor.getModel();
          const at = text.indexOf('§');
          model.setValue(text.replace('§', ''));
          editor.setPosition(model.getPositionAt(at));
          editor.focus();
        }""",
        text,
    )


def with_cursor(step: str, arguments: str) -> str:
    """The example with ``arguments`` as the ``with`` of the step and the mark in it."""
    lines = EXAMPLE.split("\n")
    start = next(i for i, x in enumerate(lines) if x.startswith(f"  {step}:"))
    row = next(i for i in range(start, len(lines)) if lines[i].lstrip().startswith("with:"))
    lines[row] = f"    with: {{{arguments}}}"
    return "\n".join(lines)


def suggestions(page: Page) -> list[str]:
    page.wait_for_selector(".suggest-widget.visible .monaco-list-row", timeout=5000)
    page.wait_for_timeout(300)  # the list settles after the answer
    rows = page.locator(".suggest-widget .monaco-list-row")
    return [
        (rows.nth(i).get_attribute("aria-label") or "").split(",")[0] for i in range(rows.count())
    ]


def test_only_what_is_available_at_the_cursor_is_suggested(
    browser: Browser, server: Server, need_editor: None
) -> None:
    """US-27: the cursor is inside ${ in step approve: only what is guaranteed there."""
    page = open_page(browser, server, "designer")
    prepare(page, with_cursor("approve", f'role: "${{{MARK}}}"'))
    page.evaluate(
        "monaco.editor.getEditors()[0].trigger('test', 'editor.action.triggerSuggest', {})"
    )
    shown = suggestions(page)
    assert shown == [
        "inputs.quarter",
        "load[*].rows",
        "load[*].count",
        "check[*].rows",
        "check[*].checked",
        "consolidate.rows",
        "consolidate.regions",
    ]
    assert not any(x.startswith(("publish", "return_to_regions", "approve")) for x in shown)
    page.close()


def test_typing_the_opening_of_a_reference_opens_the_list_and_a_choice_is_written(
    browser: Browser, server: Server, need_editor: None
) -> None:
    page = open_page(browser, server, "designer")
    prepare(page, with_cursor("publish", f'rows: "{MARK}"'))
    page.keyboard.type("${")  # the editor closes the brace; the list opens by itself
    shown = suggestions(page)
    assert "consolidate.rows" in shown and "approve.decision" in shown
    assert "publish.written" not in shown
    page.keyboard.type("approve.dec")
    page.wait_for_function(
        "document.querySelectorAll('.suggest-widget .monaco-list-row').length === 1", timeout=5000
    )
    page.keyboard.press("Enter")
    text = page.evaluate("monaco.editor.getEditors()[0].getModel().getValue()")
    assert 'with: {rows: "${approve.decision}"}' in text
    page.close()


def test_the_list_follows_the_step_the_cursor_is_in(
    browser: Browser, server: Server, need_editor: None
) -> None:
    page = open_page(browser, server, "designer")
    prepare(page, with_cursor("check", f'rows: "${{load[{MARK}"'))
    page.evaluate(
        "monaco.editor.getEditors()[0].trigger('test', 'editor.action.triggerSuggest', {})"
    )
    shown = suggestions(page)
    assert "load[region].rows" in shown and "load[region].count" in shown
    assert not any(x.startswith("check") for x in shown)
    page.close()


def test_nothing_is_suggested_outside_a_reference(
    browser: Browser, server: Server, need_editor: None
) -> None:
    page = open_page(browser, server, "designer")
    prepare(page, with_cursor("approve", f"role: chief_statistician{MARK}"))
    page.evaluate(
        "monaco.editor.getEditors()[0].trigger('test', 'editor.action.triggerSuggest', {})"
    )
    page.wait_for_timeout(1200)
    assert page.locator(".suggest-widget.visible .monaco-list-row").count() == 0
    page.close()
