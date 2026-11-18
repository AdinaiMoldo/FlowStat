"""US-45 in a browser: a core started for a demonstration lets the page sign in by role."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from playwright.sync_api import Browser

from flowstat.api.app import create_app
from tests.browser.conftest import Server, serving
from tests.browser.test_editor import BAD, set_text
from tests.unit.api.helpers import TOKENS, make_env

pytestmark = pytest.mark.browser

LOGINS = {
    "admin": TOKENS["admin"],
    "designer": TOKENS["designer"],
    "chief": TOKENS["operator"],  # the operator who holds the title of the chief statistician
    "operator": TOKENS["clerk"],
}


@pytest.fixture(scope="module")
def demonstration(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    env = make_env(Path(tmp_path_factory.mktemp("logins")))
    with serving(create_app(replace(env.config, demo_logins=LOGINS))) as url:
        yield url


def test_a_demonstration_shows_a_button_for_each_role(
    browser: Browser, demonstration: str, need_editor: None
) -> None:
    page = browser.new_page()
    page.goto(f"{demonstration}/ui/")
    page.wait_for_selector("body[data-ready]")
    page.wait_for_selector("#logins:not([hidden])")
    assert page.locator("#logins button").all_inner_texts() == [
        "Admin",
        "Designer",
        "Chief statistician",
        "Operator",
    ]
    assert page.input_value("#token") == ""  # nothing was typed
    page.close()


def test_a_button_signs_in_as_that_role_and_another_button_changes_it(
    browser: Browser, demonstration: str, need_editor: None
) -> None:
    page = browser.new_page()
    page.goto(f"{demonstration}/ui/")
    page.wait_for_selector("body[data-ready]")
    page.click("[data-login=designer]")
    assert "signed in as Designer" in page.inner_text("#who")
    set_text(page, BAD)  # a designer may check a text: the answer is its problems, not a refusal
    page.wait_for_function("document.getElementById('status').textContent.includes('error')")
    assert "may not edit" not in page.inner_text("#status")
    assert "current" in (page.get_attribute("[data-login=designer]", "class") or "")

    page.click("[data-login=operator]")  # an operator may not edit
    set_text(page, BAD + "\n")
    page.wait_for_function("document.getElementById('status').textContent.includes('may not edit')")
    assert "current" not in (page.get_attribute("[data-login=designer]", "class") or "")

    page.click("#forget")  # forgetting clears the token and the mark
    assert page.inner_text("#who") == ""
    assert page.locator("#logins button.current").count() == 0
    page.close()


def test_a_core_that_is_not_a_demonstration_shows_no_buttons(
    browser: Browser, server: Server, need_editor: None
) -> None:
    page = browser.new_page()
    page.goto(f"{server.url}/ui/")
    page.wait_for_selector("body[data-ready]")
    page.wait_for_timeout(500)  # the page asked, and the core said 404
    assert page.is_hidden("#logins")
    assert page.locator("#logins button").count() == 0
    page.close()
