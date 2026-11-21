"""US-57 in a browser: the results of a campaign on the page Campaigns."""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Browser

from tests.browser.conftest import Server
from tests.browser.test_campaigns import open_page, save_workflow
from tests.unit.actions.helpers import FIVE_ROWS, Row, write_form
from tests.unit.api.helpers import headers

pytestmark = pytest.mark.browser

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MORE: list[Row] = [("I01", "Population", 400.1), *FIVE_ROWS[1:]]


def send(server: Server, tmp_path: Path, region: str, rows: list[Row] = FIVE_ROWS) -> None:
    content = write_form(tmp_path / "made.xlsx", rows).read_bytes()
    reply = server.env.client.put(
        f"/forms/{region}/2026Q3",
        content=content,
        headers={**headers("admin"), "Content-Type": XLSX},
    )
    assert reply.status_code == 200, reply.text


def validating(server: Server) -> str:
    save_workflow(server, published=True)
    made = server.env.call("admin", "POST", "/campaigns", json={
        "name": "Quarterly Regional Statistics, Q3 2026", "year": 2026, "quarter": 3,
        "regions": ["BIS", "OSC", "BAT"], "deadline": "2026-10-31",
        "workflow": "quarterly_consolidation", "workflow_version": 1,
        "rules": "regional-indicators-v1", "preliminary_min": 2, "final_min": 3,
    })  # fmt: skip
    assert made.status_code == 201, made.text
    cid = str(made.json()["id"])
    for to in ("OPEN", "COLLECTING", "VALIDATING"):
        moved = server.env.call("admin", "POST", f"/campaigns/{cid}/transitions", json={"to": to})
        assert moved.status_code == 200
    return cid


def test_a_preliminary_result_is_made_on_the_page_and_its_figures_can_be_traced(
    browser: Browser, forms_server: Server, tmp_path: Path
) -> None:
    send(forms_server, tmp_path, "BIS")
    send(forms_server, tmp_path, "OSC", MORE)
    validating(forms_server)
    page, problems = open_page(browser, forms_server, "operator")
    page.click("[data-testid=campaign]")
    page.wait_for_selector("[data-testid=make-preliminary]:not([hidden])")
    assert "No result has been made yet" in page.inner_text("#results-empty")
    page.click("[data-testid=make-preliminary]")
    page.wait_for_selector("[data-testid=result-detail]:not([hidden])")
    row = page.locator("[data-testid=result]")
    assert row.count() == 1
    assert "#1 preliminary" in row.inner_text() and "incomplete" in row.inner_text()
    assert "2 of 3 (66.7%)" in row.inner_text()
    left = page.inner_text("#result-left")
    assert "BIS v1, OSC v1" in left and "BAT (missing: no form was sent)" in left
    first = page.locator("[data-testid=result-line]").first.inner_text()
    assert "I01 Population" in first and "800.1" in first
    assert "BIS v1: 400 + OSC v1: 400.1" in first
    assert "PRELIMINARY" in page.inner_text("#campaign-state").upper().replace(" ", "")
    assert problems == []
    page.close()


def test_a_final_result_that_the_policy_refuses_says_why_and_a_designer_only_reads(
    browser: Browser, forms_server: Server, tmp_path: Path
) -> None:
    send(forms_server, tmp_path, "BIS")
    send(forms_server, tmp_path, "OSC")
    cid = validating(forms_server)
    page, _ = open_page(browser, forms_server, "admin")
    page.click("[data-testid=campaign]")
    page.wait_for_selector("[data-testid=make-final]:not([hidden])")
    page.click("[data-testid=make-final]")
    page.wait_for_selector("#results-note:not([hidden])")
    assert "needs 3 valid forms" in page.inner_text("#results-note")
    assert page.locator("[data-testid=result]").count() == 0
    page.close()
    made = forms_server.env.call(
        "admin", "POST", f"/campaigns/{cid}/consolidations", json={"kind": "PRELIMINARY"}
    )
    assert made.status_code == 201
    send(forms_server, tmp_path, "OSC", MORE)  # a corrected form: the result is stale
    reader, problems = open_page(browser, forms_server, "designer")
    reader.click("[data-testid=campaign]")
    reader.wait_for_selector("[data-testid=result]")
    assert "stale" in reader.locator("[data-testid=result]").inner_text()
    assert reader.is_hidden("[data-testid=make-preliminary]")
    assert reader.is_hidden("[data-testid=make-final]")
    assert problems == []
    reader.close()


def test_the_workflow_is_run_from_the_campaign_and_a_second_run_takes_what_did_not_change(
    browser: Browser, forms_server: Server, tmp_path: Path
) -> None:
    from tests.unit.actions.helpers import REGION_CODES

    save_workflow(forms_server, published=True)
    made = forms_server.env.call("admin", "POST", "/campaigns", json={
        "name": "Quarterly Regional Statistics, Q3 2026", "year": 2026, "quarter": 3,
        "regions": list(REGION_CODES), "deadline": "2026-10-31",
        "workflow": "quarterly_consolidation", "workflow_version": 1,
        "rules": "regional-indicators-v1",
    })  # fmt: skip
    cid = str(made.json()["id"])
    for to in ("OPEN", "COLLECTING"):
        forms_server.env.call("admin", "POST", f"/campaigns/{cid}/transitions", json={"to": to})
    for region in REGION_CODES:
        send(forms_server, tmp_path, region)
    page, problems = open_page(browser, forms_server, "operator")
    page.click("[data-testid=campaign]")
    page.wait_for_selector("[data-testid=campaign-run]:not([hidden])")
    assert "has not been run" in page.inner_text("#campaign-runs-empty")
    page.click("[data-testid=campaign-run]")
    page.wait_for_selector("[data-testid=campaign-run-row]")
    first = page.locator("[data-testid=campaign-run-row]").first.inner_text()
    assert "WAITING" in first and "19 computed, 0 taken" in first
    send(forms_server, tmp_path, "OSH", MORE)
    page.click("[data-testid=campaign-run]")
    page.wait_for_function(
        "document.querySelectorAll('[data-testid=campaign-run-row]').length === 2"
    )
    newest = page.locator("[data-testid=campaign-run-row]").first.inner_text()
    assert "3 computed, 16 taken" in newest
    assert problems == []
    page.close()


def test_the_chief_statistician_asks_for_a_correction_and_then_approves_the_new_result(
    browser: Browser, forms_server: Server, tmp_path: Path
) -> None:
    for region in ("BIS", "OSC", "BAT"):
        send(forms_server, tmp_path, region)
    cid = validating(forms_server)
    env = forms_server.env
    assert (
        env.call(
            "admin", "POST", f"/campaigns/{cid}/consolidations", json={"kind": "FINAL"}
        ).status_code
        == 201
    )
    clerk, _ = open_page(browser, forms_server, "clerk")  # an operator without the title
    clerk.click("[data-testid=campaign]")
    clerk.click("[data-testid=result] button")
    clerk.wait_for_selector("[data-testid=result-detail]:not([hidden])")
    assert clerk.is_hidden("#result-decide")
    clerk.close()
    page, problems = open_page(browser, forms_server, "operator")  # olga, chief statistician
    page.click("[data-testid=campaign]")
    page.click("[data-testid=result] button")
    page.wait_for_selector("#result-decide:not([hidden])")
    page.click("[data-testid=decide][data-decision=REJECT]")  # no comment: refused, said why
    page.wait_for_selector("#results-note:not([hidden])")
    assert "comment is needed" in page.inner_text("#results-note")
    page.fill("#result-comment", "BIS: the population is the figure of last year")
    page.click("[data-testid=decide][data-decision=CORRECT]")
    page.wait_for_selector("[data-testid=result-decision]:not([hidden])")
    assert "A correction was asked for by olga" in page.inner_text("[data-testid=result-decision]")
    assert "figure of last year" in page.inner_text("[data-testid=result-decision]")
    assert page.is_hidden("#result-decide")
    send(forms_server, tmp_path, "BIS", MORE)
    env.call("admin", "POST", f"/campaigns/{cid}/transitions", json={"to": "VALIDATING"})
    assert (
        env.call(
            "admin", "POST", f"/campaigns/{cid}/consolidations", json={"kind": "FINAL"}
        ).status_code
        == 201
    )
    page.reload()
    page.wait_for_selector("body[data-ready]")
    page.wait_for_selector("[data-testid=result][data-number='2']")
    page.click("[data-testid=result][data-number='2'] button")
    page.wait_for_selector("#result-decide:not([hidden])")
    page.click("[data-testid=decide][data-decision=APPROVE]")
    page.wait_for_function(
        "document.querySelector('[data-testid=result-decision]').textContent"
        ".startsWith('Approved by olga')"
    )
    assert [p for p in problems if "422" not in p] == []  # the refused rejection is meant
    page.close()
