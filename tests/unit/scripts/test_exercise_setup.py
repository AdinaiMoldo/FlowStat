"""The set-up of the usability exercise gives a core in which the reference solution works (NFR-06).

A real core process is started, the reference solution is saved and published as a designer would
do it on the page, and ``check`` runs it with both answers.
"""

from __future__ import annotations

import importlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load(name: str) -> ModuleType:
    """A script of ``scripts/`` by name; the scripts import each other from their own folder."""
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        return importlib.import_module(name)
    finally:
        sys.path.remove(str(ROOT / "scripts"))


demo = load("demo")
exercise_setup = load("exercise_setup")

SOLUTION = (ROOT / "examples" / "exercise_one_region.yaml").read_text(encoding="utf8")
NAME = "one_region_publication"


def test_the_reference_solution_published_in_the_prepared_core_gives_both_ends(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    setup = exercise_setup.prepare(tmp_path / "data", demo.free_port())
    try:
        designer = setup.tokens["designer"]
        saved = demo.http(
            "POST", f"{setup.base}/workflows/{NAME}/versions", designer, {"text": SOLUTION}
        )
        assert (
            saved[0] == 201 and saved[1]["diagnostics"] == []
        )  # the catalogue and actions are there
        published = demo.http("POST", f"{setup.base}/workflows/{NAME}/versions/1/publish", designer)
        assert published[0] == 200
        assert exercise_setup.check(tmp_path / "data", NAME) == 0
    finally:
        setup.process.terminate()
    out = capsys.readouterr().out
    assert "answer approved: status FINISHED" in out and "answer rejected: status FINISHED" in out
    assert out.count("5 steps") == 2
    approved = out.split("answer rejected")[0]
    assert "publish SUCCEEDED" in approved and "return_to_region SKIPPED" in approved
    assert "return_to_region SUCCEEDED" in out.split("answer rejected")[1]


def test_the_tokens_are_kept_for_the_observer(tmp_path: Path) -> None:
    setup = exercise_setup.prepare(tmp_path / "data", demo.free_port())
    try:
        saved = json.loads((tmp_path / "data" / "tokens.json").read_text(encoding="utf8"))
    finally:
        setup.process.terminate()
    assert saved["base"] == setup.base
    assert set(saved["tokens"]) == {"admin", "designer", "chief", "operator"}


def named(name: str) -> str:
    """The reference solution under another name (the name of the text is the one of the URL)."""
    return SOLUTION.replace(f"workflow: {NAME}", f"workflow: {name}")


def test_a_name_that_is_not_there_is_answered_with_the_names_that_are(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    setup = exercise_setup.prepare(tmp_path / "data", demo.free_port())
    try:
        designer = setup.tokens["designer"]
        for name in ("only_a_draft", "one_region"):
            url = f"{setup.base}/workflows/{name}/versions"
            assert demo.http("POST", url, designer, {"text": named(name)})[0] == 201
        publish = f"{setup.base}/workflows/one_region/versions/1/publish"
        assert demo.http("POST", publish, designer)[0] == 200
        assert exercise_setup.main(["check", "one_regoin", "--dir", str(tmp_path / "data")]) == 1
    finally:
        setup.process.terminate()
    out = capsys.readouterr().out
    assert "no published workflow 'one_regoin'" in out
    assert "Published: one_region" in out and "Saved but not published: only_a_draft" in out


def test_the_folder_of_an_earlier_exercise_is_cleared_with_everything_in_it(tmp_path: Path) -> None:
    work = tmp_path / "data"
    (work / "forms").mkdir(parents=True)
    (work / "forms" / "a.xlsx").write_bytes(b"x")
    (work / "flowstat.db").write_bytes(b"x")
    (work / "tokens.json").write_text("{}", encoding="utf8")
    exercise_setup.clear(work)
    assert not work.exists()
    exercise_setup.clear(work)  # nothing there: nothing to do


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="here the same process starts the core twice and keeps its database file locked; a "
    "person starts the script in a new process each time (the clearing itself is tested above)",
)
def test_a_second_start_in_the_same_folder_gives_a_clean_core(tmp_path: Path) -> None:
    work = tmp_path / "data"
    first = exercise_setup.prepare(work, demo.free_port())
    first.process.terminate()
    first.process.wait()
    designer = first.tokens["designer"]
    assert designer
    second = exercise_setup.prepare(work, demo.free_port())  # the users of the first are gone
    try:
        assert second.tokens["designer"] != designer
        listing = demo.http("GET", f"{second.base}/workflows", second.tokens["designer"])
        assert listing == (200, [])
    finally:
        second.process.terminate()


def test_a_folder_that_is_not_an_earlier_exercise_is_not_cleared(tmp_path: Path) -> None:
    work = tmp_path / "mine"
    work.mkdir()
    (work / "thesis.txt").write_text("not to be deleted", encoding="utf8")
    with pytest.raises(demo.DemoError, match="not the folder of an earlier exercise"):
        exercise_setup.prepare(work, demo.free_port())
    assert (work / "thesis.txt").read_text(encoding="utf8") == "not to be deleted"


def test_a_port_that_already_answers_is_refused_and_nothing_is_cleared(tmp_path: Path) -> None:
    class Answer(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args: object) -> None:
            return None

    server = HTTPServer(("127.0.0.1", 0), Answer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    work = tmp_path / "data"
    work.mkdir()
    (work / "tokens.json").write_text("{}", encoding="utf8")
    try:
        with pytest.raises(demo.DemoError, match="already answers on port"):
            exercise_setup.prepare(work, server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
    assert (work / "tokens.json").is_file()  # the folder of the earlier start was left as it was


def test_a_demonstration_start_gives_the_page_its_logins_and_the_exercise_does_not(
    tmp_path: Path,
) -> None:
    plain = exercise_setup.prepare(tmp_path / "plain", demo.free_port())
    try:
        assert (
            demo.http("GET", f"{plain.base}/demo/logins")[0] == 404
        )  # the exercise: paste a token
    finally:
        plain.process.terminate()
    shown = exercise_setup.prepare(tmp_path / "shown", demo.free_port(), logins=True)
    try:
        status, body = demo.http("GET", f"{shown.base}/demo/logins")
        assert status == 200
        assert {item["name"]: item["token"] for item in body["logins"]} == shown.tokens
    finally:
        shown.process.terminate()


def test_every_user_of_a_start_can_sign_in_with_its_own_password(tmp_path: Path) -> None:
    setup = exercise_setup.prepare(tmp_path / "data", demo.free_port())
    try:
        assert set(setup.passwords) == set(exercise_setup.USERS)
        for key, (name, role, titles) in exercise_setup.USERS.items():
            status, body = demo.http(
                "POST",
                f"{setup.base}/login",
                None,
                {"name": name, "password": setup.passwords[key]},
            )
            assert status == 200, (name, body)
            assert (body["name"], body["role"], tuple(body["titles"])) == (name, role, titles)
        wrong = demo.http("POST", f"{setup.base}/login", None, {"name": "dina", "password": "nope"})
        assert wrong[0] == 401
        saved = json.loads((tmp_path / "data" / "tokens.json").read_text(encoding="utf8"))
        assert saved["passwords"] == setup.passwords
    finally:
        setup.process.terminate()
