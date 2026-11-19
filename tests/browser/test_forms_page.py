"""US-50 in a browser: the page Forms sends the forms of the regions, shows which came and what
rules they break, and the run that follows reads exactly what was sent."""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

from tests.browser.conftest import Server, use_token
from tests.unit.api.helpers import EXAMPLE, NAME, TOKENS

pytestmark = pytest.mark.browser


def open_forms(browser: Browser, server: Server, role: str = "operator") -> Page:
    page = browser.new_page()
    page.goto(f"{server.url}/ui/?plain")
    page.wait_for_selector("body[data-ready]")
    use_token(page, TOKENS[role], "forms")
    page.wait_for_selector("[data-testid=form]")
    return page


def files(folder: Path) -> list[str]:
    return sorted(str(p) for p in folder.glob("*.xlsx"))


def received(page: Page) -> list[str]:
    found: list[str] = page.eval_on_selector_all(
        "[data-testid=form]",
        "rows => rows.filter(r => r.querySelector('.badge.good')"
        " && r.querySelector('.badge.good').textContent === 'received').map(r => r.dataset.region)",
    )
    return found


def test_a_new_quarter_shows_nine_regions_that_have_sent_nothing(
    browser: Browser, forms_server: Server
) -> None:
    page = open_forms(browser, forms_server)
    assert page.locator("[data-testid=form]").count() == 9
    assert received(page) == []
    assert "0 of 9" in page.inner_text("#forms-count")
    assert "at least 7" in page.inner_text("#forms-note")
    page.close()


def test_several_files_chosen_together_go_to_the_regions_their_names_say(
    browser: Browser, forms_server: Server, sets: dict[str, Path]
) -> None:
    page = open_forms(browser, forms_server)
    page.set_input_files("#forms-files", files(sets["good"]))
    page.wait_for_function(
        "document.getElementById('forms-count').textContent.startsWith('9 of 9')"
    )
    assert len(received(page)) == 9
    assert page.locator("[data-testid=rules] .badge.good").count() == 9
    assert "run can be started" in page.inner_text("#forms-note")
    assert (forms_server.env.config.data_dir / "BIS" / "2025Q1.xlsx").is_file()  # where a run reads
    page.close()


def test_six_forms_are_not_enough_and_the_page_says_so(
    browser: Browser, forms_server: Server, sets: dict[str, Path]
) -> None:
    page = open_forms(browser, forms_server)
    page.set_input_files("#forms-files", files(sets["six-regions"]))
    page.wait_for_function(
        "document.getElementById('forms-count').textContent.startsWith('6 of 9')"
    )
    assert "at least 7" in page.inner_text("#forms-note")
    assert sorted(received(page)) == ["BAT", "BIS", "ISK", "JAL", "OSC", "OSH"]
    page.close()


def test_a_form_with_a_mistake_is_accepted_and_shown_with_its_rule(
    browser: Browser, forms_server: Server, sets: dict[str, Path]
) -> None:
    page = open_forms(browser, forms_server)
    page.set_input_files("#forms-files", files(sets["with-a-mistake"]))
    page.wait_for_function(
        "document.getElementById('forms-count').textContent.startsWith('9 of 9')"
    )
    row = page.locator("[data-testid=form][data-region=BAT]")
    assert "breaks the rules" in row.inner_text()
    assert "value is negative" in row.locator("[data-testid=rules]").inner_text()
    assert page.locator("[data-testid=rules] .badge.bad").count() == 1
    assert "BAT breaks the rules" in page.inner_text("#forms-note")
    page.close()


def test_one_file_is_sent_for_one_region_replaced_and_removed(
    browser: Browser, forms_server: Server, sets: dict[str, Path]
) -> None:
    page = open_forms(browser, forms_server)
    page.set_input_files("input.file[data-region=OSC]", str(sets["good"] / "OSC.xlsx"))
    page.wait_for_function(
        "document.getElementById('forms-count').textContent.startsWith('1 of 9')"
    )
    row = page.locator("[data-testid=form][data-region=OSC]")
    assert "5" in row.inner_text()  # the five rows of the form
    page.set_input_files("input.file[data-region=OSC]", str(sets["good"] / "BIS.xlsx"))  # replaced
    page.wait_for_function("document.getElementById('forms-status').textContent.includes('1 form')")
    assert received(page) == ["OSC"]
    row.get_by_text("Remove").click()
    page.wait_for_function(
        "document.getElementById('forms-count').textContent.startsWith('0 of 9')"
    )
    page.close()


def test_a_file_that_is_not_a_form_is_told_in_words_and_changes_nothing(
    browser: Browser, forms_server: Server, tmp_path: Path
) -> None:
    page = open_forms(browser, forms_server)
    text = tmp_path / "BIS.xlsx"
    text.write_text("this is not a workbook", encoding="utf8")
    page.set_input_files("#forms-files", str(text))
    page.wait_for_selector("[data-testid=form-error]")
    assert "not a valid xlsx" in page.inner_text("[data-testid=form-error]")
    assert received(page) == []
    other = tmp_path / "LONDON.xlsx"  # a name that is no region of the catalogue
    other.write_bytes(text.read_bytes())
    page.set_input_files("#forms-files", str(other))
    page.wait_for_function(
        "document.getElementById('forms-status').textContent.includes('no region for')"
    )
    page.close()


def test_a_designer_sees_the_forms_but_has_nothing_to_send_them_with(
    browser: Browser, forms_server: Server
) -> None:
    page = open_forms(browser, forms_server, "designer")
    assert page.locator("[data-testid=form]").count() == 9
    assert page.is_hidden("#forms-send")
    assert page.locator("input.file[data-region]").count() == 0
    page.close()


def test_the_forms_sent_on_the_page_are_what_the_run_reads(
    browser: Browser, forms_server: Server, sets: dict[str, Path]
) -> None:
    env = forms_server.env
    env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish")
    page = open_forms(browser, forms_server)
    page.set_input_files("#forms-files", files(sets["with-a-mistake"]))
    page.wait_for_function(
        "document.getElementById('forms-count').textContent.startsWith('9 of 9')"
    )
    started = env.call(
        "operator", "POST", f"/workflows/{NAME}/runs",
        json={"version": 1, "inputs": {"quarter": "2025Q1"}},
    )  # fmt: skip
    assert started.status_code == 202
    page.evaluate("id => { location.hash = '#/runs/' + id; }", started.json()["run_id"])
    page.wait_for_selector("[data-testid=step][data-id='check[BAT]']", timeout=20000)
    page.wait_for_function(
        "document.querySelector(\"[data-testid=step][data-id='check[BAT]']\").textContent"
        ".includes('value is negative')",
        timeout=20000,
    )
    page.close()


def test_the_rules_of_a_campaign_tell_where_a_form_is_wrong_and_why(
    browser: Browser, forms_server: Server, tmp_path: Path
) -> None:
    from tests.unit.actions.helpers import write_form

    env = forms_server.env
    assert (
        env.call(
            "designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE}
        ).status_code
        == 201
    )
    made = env.call(
        "admin",
        "POST",
        "/campaigns",
        json={
            "name": "Q1 2025",
            "year": 2025,
            "quarter": 1,
            "regions": ["BIS", "OSC", "BAT"],
            "deadline": "2025-04-30",
            "workflow": NAME,
            "workflow_version": 1,
            "rules": "regional-indicators-v1",
        },
    )
    assert made.status_code == 201, made.text
    five = [
        ("I01", "Population", 400), ("I02", "Households", 111.5), ("I03", "Births", 6.5),
        ("I04", "Deaths", 2.5), ("I05", "Marriages", 2.75),
    ]  # fmt: skip
    write_form(tmp_path / "BIS.xlsx", five)
    write_form(
        tmp_path / "OSC.xlsx", [*five[:2], ("I03", "Births", 450), *five[3:]]
    )  # births > people
    write_form(tmp_path / "BAT.xlsx", [*five[:3], ("X9", "Other", 1)])  # two missing, one unknown
    page = open_forms(browser, forms_server)
    page.set_input_files(
        "#forms-files", [str(tmp_path / f"{r}.xlsx") for r in ("BIS", "OSC", "BAT")]
    )
    page.wait_for_function(
        "document.getElementById('forms-count').textContent.startsWith('3 of 9')"
    )
    bis = page.locator("[data-testid=form][data-region=BIS] [data-testid=rules]")
    assert "passes the rules" in bis.inner_text()
    osc = page.locator("[data-testid=form][data-region=OSC] [data-testid=rules]")
    assert "breaks the rules" in osc.inner_text()
    assert "C4 (I03): 450 is more than 400 in I01" in osc.inner_text()
    tag = osc.locator("li[data-code=VAL-REL-001] .code")
    assert tag.inner_text() == "VAL-REL-001"
    assert "found 450" in (tag.get_attribute("title") or "")
    assert "expected at most 400 (I01)" in (tag.get_attribute("title") or "")
    bat = page.locator("[data-testid=form][data-region=BAT] [data-testid=rules]")
    assert "I04 Deaths is missing" in bat.inner_text()
    assert bat.locator("li[data-severity=warning]").count() == 1  # the code X9
    assert bat.locator("li[data-severity=error]").count() == 2
    page.close()


def test_a_region_that_sends_again_has_a_history_and_the_page_says_what_was_fixed(
    browser: Browser, forms_server: Server, tmp_path: Path
) -> None:
    from tests.unit.actions.helpers import FIVE_ROWS, write_form

    env = forms_server.env
    assert (
        env.call(
            "designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE}
        ).status_code
        == 201
    )
    made = env.call(
        "admin",
        "POST",
        "/campaigns",
        json={
            "name": "Q1 2025", "year": 2025, "quarter": 1, "regions": ["BIS", "OSC", "BAT"],
            "deadline": "2025-04-30", "workflow": NAME, "workflow_version": 1,
            "rules": "regional-indicators-v1",
        },
    )  # fmt: skip
    assert made.status_code == 201, made.text
    first = tmp_path / "first" / "BAT.xlsx"
    second = tmp_path / "second" / "BAT.xlsx"
    write_form(first, [*FIVE_ROWS[:3], ("I04", "Deaths", -2.5), FIVE_ROWS[4]])  # deaths below zero
    write_form(second, FIVE_ROWS)
    page = open_forms(browser, forms_server)
    bat = page.locator("[data-testid=form][data-region=BAT]")
    assert bat.locator("[data-testid=history-toggle]").count() == 0  # nothing sent yet
    page.set_input_files("#forms-files", [str(first)])
    page.wait_for_function(
        "document.getElementById('forms-count').textContent.startsWith('1 of 9')"
    )
    assert "breaks the rules" in bat.inner_text() and "v1" in bat.inner_text()
    page.set_input_files("#forms-files", [str(second)])
    page.wait_for_selector("[data-testid=form][data-region=BAT] [data-testid=version]:text('v2')")
    assert "passes the rules" in bat.inner_text()
    bat.locator("[data-testid=history-toggle]").click()
    history = page.locator("[data-testid=history][data-region=BAT]")
    page.wait_for_selector("[data-testid=history][data-region=BAT] [data-testid=comparison]")
    versions = history.locator("li[data-version]")
    assert versions.count() == 2
    assert (
        "v2 · current" in versions.nth(0).inner_text()
        and "no errors" in versions.nth(0).inner_text()
    )
    assert (
        "v1 · replaced" in versions.nth(1).inner_text()
        and "1 error" in versions.nth(1).inner_text()
    )
    assert "sent by olga" in versions.nth(0).inner_text()
    said = history.locator("[data-testid=comparison]").inner_text()
    assert "Compared with v1:" in said and "fixed 1 (VAL-RANGE-001 I04)" in said
    assert "none still there" in said and "none new" in said
    bat.locator("[data-testid=history-toggle]").click()  # closes it again
    assert page.locator("[data-testid=history]").count() == 0
    page.close()


def test_an_operator_of_one_office_is_offered_only_the_form_of_that_office(
    browser: Browser, forms_server: Server, sets: dict[str, Path]
) -> None:
    """US-64: Choose and Remove are there for OSH only; the other rows say whose they are."""
    from flowstat.repo.db import open_database
    from flowstat.service.users import UserService
    from tests.unit.api.helpers import T

    token = "e5" * 32
    db = open_database(forms_server.env.config.db_path)
    UserService(db).add("aibek", "operator", set(), token, T, regions={"OSH"})
    db.close()
    first = open_forms(browser, forms_server, "admin")
    first.set_input_files("#forms-files", files(sets["good"]))
    first.wait_for_function(
        "document.getElementById('forms-count').textContent.startsWith('9 of 9')"
    )
    first.close()
    page = browser.new_page()
    page.goto(f"{forms_server.url}/ui/?plain")
    page.wait_for_selector("body[data-ready]")
    use_token(page, token, "forms")
    page.wait_for_selector("[data-testid=form]")
    mine = page.locator("[data-testid=form][data-region=OSH]")
    other = page.locator("[data-testid=form][data-region=BIS]")
    assert mine.locator("input[type=file]").count() == 1
    assert mine.get_by_role("button", name="Remove").count() == 1
    assert other.locator("input[type=file]").count() == 0
    assert other.get_by_role("button", name="Remove").count() == 0
    assert "another office" in other.inner_text()
    mine.get_by_role("button", name="Remove").click()
    page.wait_for_function(
        "document.getElementById('forms-count').textContent.startsWith('8 of 9')"
    )
    assert "OSH" not in received(page) and "BIS" in received(page)
    page.close()
