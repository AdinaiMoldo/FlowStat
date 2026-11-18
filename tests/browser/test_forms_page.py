"""US-50 in a browser: the page Forms sends the forms of the regions, shows which came and what
rules they break, and the run that follows reads exactly what was sent."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

from tests.browser.conftest import Server, serving, use_token
from tests.unit.api.helpers import EXAMPLE, NAME, TOKENS, make_env

pytestmark = pytest.mark.browser

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def sets(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        make_forms = importlib.import_module("make_forms")
    finally:
        sys.path.remove(str(ROOT / "scripts"))
    out = tmp_path_factory.mktemp("sample-forms")
    make_forms.write_sets(out)
    return {name: out / name for name in ("good", "six-regions", "with-a-mistake")}


@pytest.fixture
def forms_server(tmp_path: Path) -> Iterator[Server]:
    """A core of its own for each test: what one test sends must not be there for the next."""
    env = make_env(tmp_path)
    with serving(env.client.app) as url:
        yield Server(env, url)


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
    assert "break the rules" in page.inner_text("#forms-note")
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
