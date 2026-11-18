"""The page of the editor is static, public and made only of files that exist (US-26)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from flowstat.api.ui import UI_DIR
from tests.unit.api.helpers import EXAMPLE, Env, make_env


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return make_env(tmp_path)


def test_the_page_is_served_without_a_token(env: Env) -> None:
    reply = env.client.get("/ui/")
    assert reply.status_code == 200
    assert reply.headers["content-type"].startswith("text/html")
    assert "FlowStat" in reply.text


def test_the_root_sends_a_browser_to_the_page(env: Env) -> None:
    reply = env.client.get("/", follow_redirects=False)
    assert reply.status_code in (302, 307)
    assert reply.headers["location"] == "/ui/"


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("/ui/main.js", "text/javascript"),
        ("/ui/api.js", "text/javascript"),
        ("/ui/editor.js", "text/javascript"),
        ("/ui/ops.js", "text/javascript"),
        ("/ui/live.js", "text/javascript"),
        ("/ui/router.js", "text/javascript"),
        ("/ui/home.js", "text/javascript"),
        ("/ui/format.js", "text/javascript"),
        ("/ui/forms.js", "text/javascript"),
        ("/ui/style.css", "text/css"),
    ],
)
def test_scripts_and_styles_have_the_type_a_browser_accepts(env: Env, path: str, kind: str) -> None:
    """A module script served as text/plain is refused by the browser (it happens on Windows)."""
    reply = env.client.get(path)
    assert reply.status_code == 200
    assert reply.headers["content-type"].split(";")[0] == kind


def test_a_path_outside_the_folder_is_not_served(env: Env) -> None:
    for path in ("/ui/../app.py", "/ui/%2e%2e/app.py", "/ui/..%2fapp.py", "/ui/nothing.js"):
        assert env.client.get(path).status_code == 404, path


def test_the_page_names_only_files_that_exist() -> None:
    """Every local file the page loads is in the folder; the fetched ones are the exception."""
    page = (UI_DIR / "index.html").read_text(encoding="utf8")
    names = re.findall(r'(?:src|href)="([^"]+)"', page)
    assert names
    for name in names:
        if name.startswith(("vendor/", "#")):  # fetched files; addresses inside the page
            continue
        assert (UI_DIR / name).is_file(), name
    scripts = re.findall(r'from "\./([\w.]+)"', (UI_DIR / "main.js").read_text(encoding="utf8"))
    for name in scripts:
        assert (UI_DIR / name).is_file(), name


def test_no_file_of_the_page_holds_a_token_or_stores_it_for_good() -> None:
    for path in UI_DIR.glob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf8")
            assert not re.search(r"\b[0-9a-f]{64}\b", text), path.name
            assert "localStorage." not in text, path.name


def test_the_new_workflow_of_the_page_passes_the_checks(env: Env) -> None:
    source = (UI_DIR / "main.js").read_text(encoding="utf8")
    found = re.search(r"const SKELETON = `(.*?)`;", source, re.DOTALL)
    assert found
    reply = env.call("designer", "POST", "/validate", {"text": found.group(1)})
    assert reply.status_code == 200
    assert reply.json()["diagnostics"] == []


def test_the_example_of_the_page_is_the_example_of_the_repository() -> None:
    """The button "Example" holds a copy of examples/quarterly_consolidation.yaml (US-44)."""
    source = (UI_DIR / "example.js").read_text(encoding="utf8")
    match = re.search(r"export const EXAMPLE = `(.*)`;\n", source, re.DOTALL)
    assert match
    text = match.group(1).replace("\\${", "${").replace("\\`", "`").replace("\\\\", "\\")
    assert text == EXAMPLE
