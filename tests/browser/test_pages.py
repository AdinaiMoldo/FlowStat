"""US-47 in a browser: signing in with a name and a password, the addresses of the pages, the
menu of a role, and the home page."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

from flowstat.repo.db import open_database
from flowstat.service.users import UserService
from tests.browser.conftest import Server, serving, sign_out
from tests.unit.actions.helpers import write_all_forms
from tests.unit.api.helpers import EXAMPLE, NAME, Env, make_env

pytestmark = pytest.mark.browser

PASSWORD = "correct horse battery"


@pytest.fixture(scope="module")
def pages(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    env = make_env(Path(tmp_path_factory.mktemp("pages")))
    db = open_database(env.config.db_path)
    users = UserService(db)
    for name in ("ada", "dina", "olga"):
        users.set_password(name, PASSWORD, name.encode().ljust(16, b"."))
    db.close()
    with serving(env.client.app) as url:
        yield Server(env, url)


def open_login(browser: Browser, server: Server, address: str = "") -> Page:
    page = browser.new_page()
    page.goto(f"{server.url}/ui/?plain{address}")
    page.wait_for_selector("body[data-ready]")
    return page


def sign_in(page: Page, name: str, password: str = PASSWORD) -> None:
    page.fill("#login-name", name)
    page.fill("#login-password", password)
    page.click("#login-submit")


def view(page: Page) -> str:
    found: str = page.evaluate("document.body.dataset.view")
    return found


def wait_for_view(page: Page, name: str) -> None:
    page.wait_for_function("name => document.body.dataset.view === name", arg=name)


def test_a_page_that_is_opened_without_signing_in_is_the_sign_in_page(
    browser: Browser, pages: Server
) -> None:
    page = open_login(browser, pages)
    assert view(page) == "login"
    assert page.is_hidden("#top")  # no menu for a person who is not in
    assert page.is_visible("#login-name") and page.is_visible("#login-password")
    page.close()


def test_a_name_and_a_password_open_the_home_page_for_that_person(
    browser: Browser, pages: Server
) -> None:
    page = open_login(browser, pages)
    sign_in(page, "olga")
    wait_for_view(page, "home")
    assert page.url.endswith("#/home")
    assert "Olga" in page.inner_text("#who") and "chief statistician" in page.inner_text("#who")
    assert page.inner_text("#home-hello").endswith(", Olga")
    assert page.input_value("#login-password") == ""  # the password is not kept in the page
    page.close()


def test_a_wrong_password_is_told_in_words_and_nobody_is_signed_in(
    browser: Browser, pages: Server
) -> None:
    page = open_login(browser, pages)
    sign_in(page, "olga", "not the password")
    page.wait_for_selector("#login-error:not([hidden])")
    assert page.inner_text("#login-error") == "The name or the password is wrong."
    assert view(page) == "login"
    assert page.evaluate("sessionStorage.length") == 0
    sign_in(page, "olga")  # and the right one is accepted after it
    wait_for_view(page, "home")
    page.close()


def test_a_token_that_is_not_accepted_is_told_too(browser: Browser, pages: Server) -> None:
    page = open_login(browser, pages)
    page.click("#token-toggle")
    page.fill("#token", "ff" * 32)
    page.click("#sign-in button[type=submit]")
    page.wait_for_selector("#login-error:not([hidden])")
    assert page.inner_text("#login-error") == "This token is not accepted."
    assert page.evaluate("sessionStorage.length") == 0  # a token that does not work is not kept
    page.close()


def test_signing_out_ends_the_session_and_closes_the_pages(browser: Browser, pages: Server) -> None:
    page = open_login(browser, pages)
    sign_in(page, "ada")
    wait_for_view(page, "home")
    token = page.evaluate("sessionStorage.getItem('flowstat.token')")
    assert token
    sign_out(page)
    assert view(page) == "login" and page.evaluate("sessionStorage.length") == 0
    # the session is ended at the core, not only forgotten by the page
    again = pages.env.client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert again.status_code == 401
    page.evaluate("location.hash = '#/runs'")
    wait_for_view(page, "login")  # a page of the system is not open to a person who is out
    page.close()


def test_an_address_that_is_followed_before_signing_in_is_opened_after(
    browser: Browser, pages: Server
) -> None:
    page = open_login(browser, pages, "#/runs/some-run")
    assert view(page) == "login"
    assert "next=" in page.url
    sign_in(page, "olga")
    wait_for_view(page, "runs")
    assert page.url.endswith("#/runs/some-run")
    page.close()


def test_a_reload_keeps_the_person_on_the_page_they_were_on(
    browser: Browser, pages: Server
) -> None:
    page = open_login(browser, pages)
    sign_in(page, "olga")
    wait_for_view(page, "home")
    page.click("#tab-services")
    wait_for_view(page, "services")
    page.reload()
    page.wait_for_selector("body[data-ready]")
    assert view(page) == "services"
    assert "Olga" in page.inner_text("#who")
    page.close()


def test_the_back_and_forward_buttons_move_between_the_pages(
    browser: Browser, pages: Server
) -> None:
    page = open_login(browser, pages)
    sign_in(page, "olga")
    wait_for_view(page, "home")
    page.click("#tab-services")
    wait_for_view(page, "services")
    page.click("#tab-runs")
    wait_for_view(page, "runs")
    page.go_back()
    wait_for_view(page, "services")
    page.go_back()
    wait_for_view(page, "home")
    page.go_forward()
    wait_for_view(page, "services")
    assert page.locator("#tab-services").get_attribute("aria-current") == "page"
    assert page.title() == "Services · FlowStat"
    page.close()


def test_the_menu_shows_only_the_pages_the_role_may_use(browser: Browser, pages: Server) -> None:
    operator = open_login(browser, pages)
    sign_in(operator, "olga")
    wait_for_view(operator, "home")
    assert operator.is_visible("#tab-runs") and operator.is_visible("#tab-migration")
    assert operator.is_visible("#tab-editor")
    assert operator.is_hidden("#tab-statistics")
    assert operator.is_hidden("[data-needs='workflows:write']")  # no "New workflow" for an operator
    operator.close()

    admin = open_login(browser, pages)
    sign_in(admin, "ada")
    wait_for_view(admin, "home")
    assert admin.is_visible("#tab-statistics")
    assert admin.is_visible("#home-actions >> text=Load the example")
    admin.close()


def test_a_session_that_the_core_ends_sends_the_person_to_sign_in_again(
    browser: Browser, pages: Server
) -> None:
    page = open_login(browser, pages)
    sign_in(page, "olga")
    wait_for_view(page, "home")
    page.click("#tab-runs")
    wait_for_view(page, "runs")
    token = page.evaluate("sessionStorage.getItem('flowstat.token')")
    ended = pages.env.client.post("/logout", headers={"Authorization": f"Bearer {token}"})
    assert ended.status_code == 204  # the session was ended elsewhere than in the page
    page.click("#runs-refresh")
    wait_for_view(page, "login")
    assert page.is_visible("#login-note")
    sign_in(page, "olga")
    wait_for_view(page, "runs")  # back where the person was
    page.close()


def waiting_run(server: Server) -> str:
    """A run of the example that has reached the approval of the chief statistician."""
    env: Env = server.env
    write_all_forms(env.config.data_dir, "2025Q1")
    if env.call("designer", "GET", f"/workflows/{NAME}/versions/1").status_code != 200:
        env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
        env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish")
    started = env.call(
        "designer", "POST", f"/workflows/{NAME}/runs",
        json={"version": 1, "inputs": {"quarter": "2025Q1"}},
    )  # fmt: skip
    assert started.status_code == 202, started.text
    run_id: str = started.json()["run_id"]
    for _ in range(200):
        listing = env.call("admin", "GET", "/runs").json()
        if any(r["run_id"] == run_id and r["status"] == "WAITING" for r in listing):
            return run_id
        time.sleep(0.1)
    pytest.fail("the run did not reach the approval")


def test_the_home_page_counts_what_there_is_and_leads_to_what_waits(
    browser: Browser, pages: Server
) -> None:
    run_id = waiting_run(pages)
    page = open_login(browser, pages)
    sign_in(page, "olga")
    wait_for_view(page, "home")
    page.wait_for_function("document.getElementById('count-runs').textContent !== '\u2013'")
    assert int(page.inner_text("#count-workflows")) >= 1
    assert int(page.inner_text("#count-waiting")) >= 1
    item = page.locator(f"[data-testid=waiting][data-run={run_id}]")
    item.wait_for()
    assert page.locator(f"[data-testid=recent][data-run={run_id}]").count() == 1
    item.get_by_text("Review").click()  # the link goes to the run
    wait_for_view(page, "runs")
    assert page.url.endswith(f"#/runs/{run_id}")
    page.wait_for_selector("[data-testid=ask]", timeout=10000)  # and there it can be answered
    page.close()
