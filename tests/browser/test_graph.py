"""US-28 in a browser: the preview draws every step and every dependency."""

from __future__ import annotations

import time
from typing import Any

import pytest
from playwright.sync_api import Browser, Page

from tests.browser.conftest import Server
from tests.browser.test_editor import BAD, GOOD, open_page, set_text
from tests.unit.api.helpers import EXAMPLE

pytestmark = pytest.mark.browser


def drawn(page: Page, expression: str) -> Any:
    return page.evaluate(f"document.getElementById('graph').cy.{expression}")


def tap(page: Page, node: str) -> None:
    """Tap a node. The function returns nothing: a Cytoscape collection is a huge object, and
    Playwright would take seconds to carry it back."""
    page.evaluate(
        "id => { document.getElementById('graph').cy.getElementById(id).emit('tap'); }", node
    )


def show_graph(page: Page) -> None:
    page.click("#preview")
    page.wait_for_function("document.getElementById('graph').cy !== undefined")


def wait_for_nodes(page: Page, count: int) -> None:
    page.wait_for_function(
        f"document.getElementById('graph').cy.nodes().length === {count}", timeout=5000
    )


def test_every_step_instance_is_a_node_and_every_dependency_an_arrow(
    browser: Browser, server: Server, need_editor: None
) -> None:
    """US-28: a valid workflow, the preview opened: nodes, arrows, marks and groups."""
    page = open_page(browser, server, "designer")
    set_text(page, EXAMPLE)
    show_graph(page)
    # the fan-outs have nine instances each (fewer than the limit), so they start open
    wait_for_nodes(page, 22 + 2)  # 22 instances and the two boxes around the fan-outs
    assert drawn(page, "nodes().filter(n => !n.isParent()).length") == 22
    assert drawn(page, "edges().length") == 21
    assert drawn(page, "nodes('.conditional').map(n => n.id()).sort()") == [
        "publish",
        "return_to_regions",
    ]
    assert drawn(page, "nodes('.goal').map(n => n.id()).sort()") == ["publish", "return_to_regions"]
    assert drawn(page, "nodes('.fan-out').map(n => n.children().length)") == [9, 9]
    # every arrow goes down the page: the layout follows the layers
    ys = drawn(page, "edges().map(e => [e.source().position('y'), e.target().position('y')])")
    assert all(source < target for source, target in ys)
    page.close()


def test_a_click_on_a_step_moves_the_cursor_to_it(
    browser: Browser, server: Server, need_editor: None
) -> None:
    page = open_page(browser, server, "designer")
    set_text(page, EXAMPLE)
    show_graph(page)
    wait_for_nodes(page, 24)
    tap(page, "approve")
    line = page.evaluate("monaco.editor.getEditors()[0].getPosition().lineNumber")
    assert line == 22  # `approve:` in the text
    page.close()


def test_a_fan_out_collapses_and_opens_on_a_click(
    browser: Browser, server: Server, need_editor: None
) -> None:
    page = open_page(browser, server, "designer")
    set_text(page, EXAMPLE)
    show_graph(page)
    wait_for_nodes(page, 24)
    tap(page, "parent:load")
    wait_for_nodes(page, 24 - 9)  # nine instances and their box become one node
    assert drawn(page, "getElementById('group:load').hasClass('collapsed')")
    assert drawn(page, "getElementById('group:load').data('label')").startswith("load \u00d79")
    tap(page, "group:load")
    wait_for_nodes(page, 24)
    page.close()


def test_the_graph_follows_the_text_and_keeps_the_last_good_one_on_an_error(
    browser: Browser, server: Server, need_editor: None
) -> None:
    page = open_page(browser, server, "designer")
    set_text(page, GOOD)
    show_graph(page)
    wait_for_nodes(page, 1)
    set_text(page, BAD)
    page.wait_for_function("!document.getElementById('graph-note').hidden", timeout=5000)
    assert "last graph that was valid" in page.inner_text("#graph-note")
    assert drawn(page, "nodes().length") == 1
    set_text(
        page, GOOD.replace("steps:", "steps:\n  first: {action: approval.wait, with: {role: x}}")
    )
    wait_for_nodes(page, 2)
    assert page.is_hidden("#graph-note")
    page.close()


def test_a_large_fan_out_starts_collapsed_and_is_drawn_quickly(
    browser: Browser, server: Server, need_editor: None
) -> None:
    """A hundred regions are two nodes, not two hundred; the time to draw them is recorded."""
    regions = [{"code": f"R{i:03d}", "name": f"Region {i}"} for i in range(100)]
    reply = server.env.call("admin", "PUT", "/catalogue/many", {"items": regions})
    assert reply.status_code == 200
    text = EXAMPLE.replace("catalogue.regions", "catalogue.many").replace(
        "at_least: 7", "at_least: 90"
    )
    page = open_page(browser, server, "designer")
    set_text(page, text)
    started = time.monotonic()
    show_graph(page)
    wait_for_nodes(
        page, 2 + 4
    )  # the two collapsed fan-outs and consolidate, approve, publish, return
    elapsed = time.monotonic() - started
    assert drawn(page, "nodes('.collapsed').length") == 2
    tap(page, "group:load")
    wait_for_nodes(page, 100 + 1 + 1 + 4)  # one fan-out open: its box and its hundred instances
    assert elapsed < 5
    page.close()


def test_without_the_library_the_graph_is_a_list_of_layers(
    browser: Browser, server: Server
) -> None:
    page = browser.new_page()
    page.route("**/vendor/**", lambda route: route.abort())
    page.goto(f"{server.url}/ui/")
    page.wait_for_selector("body[data-ready=plain]")
    page.fill("#token", "d2" * 32)
    page.click("#sign-in button[type=submit]")
    page.fill("#plain", EXAMPLE)
    page.click("#preview")
    page.wait_for_selector("#graph .layers li", timeout=5000)
    text = page.inner_text("#graph")
    assert "layer 0: load \u00d79" in text
    assert "layer 4: publish (conditional), return_to_regions (conditional)" in text
    page.close()
