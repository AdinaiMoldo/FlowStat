"""US-31 in a browser: the services that were deployed and the runs they shipped to the core."""

from __future__ import annotations

import sqlite3

import pytest
from playwright.sync_api import Browser, Page

from flowstat.repo.deployments import Deployment, DeploymentStore
from flowstat.service.users import hash_token
from tests.browser.conftest import Server, use_token
from tests.unit.api.helpers import EXAMPLE, NAME, TOKENS, T

pytestmark = pytest.mark.browser

KEY = "k9" * 32
TRAP = '<img src=x onerror="window.pwned = 1">'


@pytest.fixture(scope="module")
def shipped(server: Server) -> str:
    """A published version, a deployment, and one run that the deployment shipped."""
    env = server.env
    env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish")
    db = sqlite3.connect(env.config.db_path, isolation_level=None)
    DeploymentStore(db).add(
        Deployment(
            "svc-7", NAME, 1, "process", "running", "/bundles/svc-7", T, T,
            endpoint="http://127.0.0.1:9100", ingest_key_sha256=hash_token(KEY),
        )
    )  # fmt: skip
    db.close()
    body = {
        "run": {
            "run_id": "from-svc-7",
            "workflow": NAME,
            "version": 1,
            "inputs": {"quarter": "2025Q1"},
            "created_at": T,
        },
        "events": [
            {"seq": 0, "kind": "RUN_STARTED", "at": 1.0},
            {"seq": 1, "kind": "STEP_STARTED", "at": 2.0, "step": "load[BIS]"},
            {
                "seq": 2, "kind": "STEP_FINISHED", "at": 3.0, "step": "load[BIS]",
                "state": "FAILED", "code": "E-FILE-001", "message": TRAP,
            },
        ],
    }  # fmt: skip
    reply = env.client.post(
        "/deployments/svc-7/events", json=body, headers={"Authorization": f"Bearer {KEY}"}
    )
    assert reply.status_code == 200, reply.text
    return "svc-7"


def open_page(browser: Browser, server: Server) -> Page:
    page = browser.new_page()
    page.goto(f"{server.url}/ui/?plain")
    page.wait_for_selector("body[data-ready]")
    use_token(page, TOKENS["operator"])
    return page


def test_the_services_view_says_what_runs_where(
    browser: Browser, server: Server, shipped: str
) -> None:
    page = open_page(browser, server)
    page.click("#tab-services")
    row = page.locator("[data-testid=service][data-id=svc-7]")
    row.wait_for()
    text = row.inner_text()
    for part in (NAME, "v1", "process", "running", "http://127.0.0.1:9100", "1"):
        assert part in text
    assert T in text  # the time of the last events
    assert page.locator("#view-editor").is_hidden() and page.locator("#view-runs").is_hidden()
    page.close()


def test_a_click_on_a_service_shows_the_runs_it_shipped_and_a_click_on_a_run_its_steps(
    browser: Browser, server: Server, shipped: str
) -> None:
    page = open_page(browser, server)
    page.click("#tab-services")
    page.click("[data-testid=service][data-id=svc-7]")
    page.wait_for_function("document.body.dataset.view === 'runs'")
    page.wait_for_function("document.getElementById('runs-from').value === 'svc-7'")
    run = page.locator("[data-testid=run][data-id=from-svc-7]")
    run.wait_for()
    assert "service svc-7" in run.inner_text()
    run.click()
    step = page.locator("[data-testid=step][data-id='load[BIS]']")
    step.wait_for()
    assert "FAILED" in step.inner_text() and "E-FILE-001" in step.inner_text()
    page.close()


def test_what_a_service_wrote_is_shown_as_text_and_never_run(
    browser: Browser, server: Server, shipped: str
) -> None:
    page = open_page(browser, server)
    page.click("#tab-runs")
    page.click("[data-testid=run][data-id=from-svc-7]")
    step = page.locator("[data-testid=step][data-id='load[BIS]']")
    step.wait_for()
    assert TRAP in step.inner_text()  # the characters are on the page ...
    assert page.locator("#run-steps img").count() == 0  # ... but they made no element
    assert page.evaluate("window.pwned") is None
    page.close()


def test_the_runs_of_the_core_can_be_told_from_the_runs_of_services(
    browser: Browser, server: Server, shipped: str
) -> None:
    page = open_page(browser, server)
    page.click("#tab-runs")
    page.locator("[data-testid=run][data-id=from-svc-7]").wait_for()
    page.select_option("#runs-from", "core")
    page.wait_for_function("document.querySelector('[data-id=from-svc-7]') === null")
    page.select_option("#runs-from", "svc-7")
    page.locator("[data-testid=run][data-id=from-svc-7]").wait_for()
    page.close()


def test_the_editor_is_still_there_after_the_other_views(
    browser: Browser, server: Server, shipped: str
) -> None:
    page = open_page(browser, server)
    page.click("#tab-runs")
    page.wait_for_function("document.body.dataset.view === 'runs'")
    page.click("#tab-editor")
    page.wait_for_function("document.body.dataset.view === 'editor'")
    assert page.locator("#view-editor").is_visible() and page.locator("#view-runs").is_hidden()
    assert page.locator("#tab-editor").get_attribute("aria-current") == "page"
    page.close()
