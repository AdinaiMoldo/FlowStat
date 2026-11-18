"""Tests of the command line: publish a workflow, run it with real actions, read the report."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from flowstat.repo.db import open_database
from flowstat.service.cli import main
from flowstat.service.users import UserService, hash_token
from tests.unit.actions.helpers import write_all_forms
from tests.unit.engine.helpers import ROOT

WORKFLOW = str(ROOT / "examples" / "quarterly_consolidation.yaml")
CATALOGUE = str(ROOT / "examples" / "catalogue.yaml")
NAME = "quarterly_consolidation"


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.db = str(tmp_path / "flowstat.db")
        self.data = tmp_path / "data"
        self.target = str(tmp_path / "target.db")

    def publish(self) -> int:
        return main(["publish", "--db", self.db, "--catalogue", CATALOGUE, WORKFLOW])

    def run(self, *extra: str, run_id: str = "run-1", quarter: str = "2025Q1") -> int:
        where = ["--db", self.db, "--data", str(self.data), "--target", self.target]
        return main(
            ["run", *where, "--run-id", run_id, "--input", f"quarter={quarter}", *extra, NAME]
        )

    def rows(self, sql: str) -> list[tuple[object, ...]]:
        db = sqlite3.connect(self.target)
        try:
            return db.execute(sql).fetchall()
        except sqlite3.OperationalError:
            return []  # no action wrote to the database, so its tables do not exist
        finally:
            db.close()


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def test_publish_prints_the_version(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    assert env.publish() == 0
    assert f"published {NAME} v1" in capsys.readouterr().out


def test_publishing_a_broken_workflow_prints_diagnostics(
    env: Env, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text("workflow: demo\nversion: 1\nsteps:\n  a:\n    action: nope.do\n", "utf8")
    assert main(["publish", "--db", env.db, str(broken)]) == 1
    out = capsys.readouterr().out
    assert "E-" in out and "line" in out


def test_a_full_run_publishes_and_reports(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    write_all_forms(env.data, "2025Q1")
    env.publish()
    capsys.readouterr()
    assert env.run("--decision", "approved") == 0
    out = capsys.readouterr().out
    assert "SUCCEEDED" in out and "consolidate" in out and "publish" in out
    assert env.rows("SELECT batch, code, value FROM published ORDER BY code") == [
        ("run-1", "I01", 900.0),
        ("run-1", "I02", 364.5),
        ("run-1", "I03", 63.0),
    ]


def test_a_missing_form_fails_its_step_with_the_path_and_the_run_goes_on(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    write_all_forms(env.data, "2025Q1", missing=["BIS", "OSC"])
    env.publish()
    capsys.readouterr()
    assert env.run("--decision", "approved") == 0  # seven of nine is enough for the quorum
    out = capsys.readouterr().out
    assert "load[BIS]" in out and "BIS/2025Q1.xlsx" in out and "E-ACT-002" in out
    assert env.rows("SELECT code, value FROM published ORDER BY code")[0] == ("I01", 700.0)


def test_too_few_forms_is_not_reported_as_success(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    write_all_forms(env.data, "2025Q1", missing=["BIS", "OSC", "BAT"])
    env.publish()
    capsys.readouterr()
    assert env.run("--decision", "approved") == 1
    assert "GOAL_NOT_REACHED" in capsys.readouterr().out
    assert env.rows("SELECT * FROM published") == []


def test_a_rejection_returns_the_forms_to_the_regions(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    write_all_forms(env.data, "2025Q1")
    env.publish()
    capsys.readouterr()
    assert env.run("--decision", "rejected") == 0
    assert "SUCCEEDED" in capsys.readouterr().out
    assert env.rows("SELECT run_id, step FROM outbox") == [("run-1", "return_to_regions")]
    assert env.rows("SELECT COUNT(*) FROM published") == [(0,)]


def test_without_a_decision_the_run_waits_for_the_chief_statistician(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    write_all_forms(env.data, "2025Q1")
    env.publish()
    capsys.readouterr()
    assert env.run() == 3
    out = capsys.readouterr().out
    assert "WAITING" in out and "approve" in out


def test_the_report_of_a_stored_run_can_be_read_again(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    write_all_forms(env.data, "2025Q1")
    env.publish()
    capsys.readouterr()
    env.run("--decision", "approved")
    first = capsys.readouterr().out
    assert main(["report", "--db", env.db, "run-1"]) == 0
    again = capsys.readouterr().out
    assert first.strip() == again.strip()


def test_unknown_workflow_and_bad_inputs_are_usage_errors(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    assert env.run() == 2
    assert NAME in capsys.readouterr().err
    env.publish()
    capsys.readouterr()
    assert env.run(quarter="nope") == 2
    assert "E-INPUT" in capsys.readouterr().err
    assert main(["report", "--db", env.db, "nope"]) == 2


def test_a_draft_cannot_be_run(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    from flowstat.service.cli import open_services

    workflows, _ = open_services(env.db)
    workflows.save_draft(NAME, Path(WORKFLOW).read_text("utf8"), "2026-11-05T10:00:00Z")
    assert env.run() == 2
    assert "publish" in capsys.readouterr().err


def test_the_report_shows_how_many_attempts_a_step_needed(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    write_all_forms(env.data, "2025Q1")
    env.publish()
    capsys.readouterr()
    env.run("--decision", "approved")
    lines = capsys.readouterr().out.splitlines()
    header = next(line for line in lines if line.startswith("step"))
    assert "tries" in header
    row = next(line for line in lines if line.startswith("load[BIS]"))
    assert row.split()[-1] == "1"  # ran once, no retry needed


# -- users --------------------------------------------------------------------------------------
def test_user_add_prints_a_token_once_and_stores_only_its_hash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = str(tmp_path / "flowstat.db")
    code = main(["user", "add", "olga", "--role", "operator", "--title", "chief_statistician",
                 "--db", db])  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0 and "chief_statistician" in out
    token = next(line.split(": ")[1] for line in out.splitlines() if line.startswith("token: "))
    assert len(token) == 64 and int(token, 16) >= 0
    users = UserService(open_database(db))
    found = users.authenticate(token)
    assert found is not None and found.name == "olga" and found.titles == {"chief_statistician"}
    stored = sqlite3.connect(db).execute("SELECT token_hash FROM users").fetchall()
    assert stored == [(hash_token(token),)] and token not in str(stored)


def test_two_users_get_different_tokens_and_a_name_is_taken_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = str(tmp_path / "flowstat.db")
    assert main(["user", "add", "ada", "--role", "admin", "--db", db]) == 0
    first = capsys.readouterr().out
    assert main(["user", "add", "dina", "--role", "designer", "--db", db]) == 0
    second = capsys.readouterr().out
    assert first.splitlines()[1] != second.splitlines()[1]
    assert main(["user", "add", "ada", "--role", "admin", "--db", db]) == 2
    assert "already a user" in capsys.readouterr().err


def test_user_add_refuses_a_bad_name_or_role(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = str(tmp_path / "flowstat.db")
    assert main(["user", "add", "Bad Name", "--role", "admin", "--db", db]) == 2
    assert "lower case" in capsys.readouterr().err
    with pytest.raises(SystemExit):  # argparse refuses a role that does not exist
        main(["user", "add", "ada", "--role", "boss", "--db", db])


def test_user_disable_and_list(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = str(tmp_path / "flowstat.db")
    main(["user", "add", "ada", "--role", "admin", "--db", db])
    token = next(
        line.split(": ")[1] for line in capsys.readouterr().out.splitlines() if "token: " in line
    )
    assert main(["user", "disable", "ada", "--db", db]) == 0
    assert UserService(open_database(db)).authenticate(token) is None
    assert main(["user", "disable", "nobody", "--db", db]) == 2
    capsys.readouterr()
    assert main(["user", "list", "--db", db]) == 0
    assert capsys.readouterr().out.split() == ["ada", "admin", "-", "disabled"]


def test_a_decision_the_step_does_not_declare_is_refused_on_run_and_resume(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    write_all_forms(env.data, "2025Q1")
    env.publish()
    capsys.readouterr()
    assert env.run("--decision", "maybe") == 3  # the run waits; nothing was accepted
    assert "does not accept the decision 'maybe'" in capsys.readouterr().err
    where = ["--db", env.db, "--data", str(env.data), "--target", env.target]
    assert main(["resume", "run-1", *where, "--decision", "maybe"]) == 2
    assert "approved, rejected" in capsys.readouterr().err
    assert main(["resume", "run-1", *where, "--decision", "approved"]) == 0


NOW = "2026-11-08T10:00:00Z"


def test_user_add_with_a_password_and_user_passwd_read_it_from_the_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import io

    db = str(tmp_path / "flowstat.db")
    monkeypatch.setattr("sys.stdin", io.StringIO("correct horse battery\n"))
    assert main(["user", "add", "dina", "--role", "designer", "--password", "--db", db]) == 0
    users = UserService(open_database(db))
    token = next(
        line.split(": ")[1] for line in capsys.readouterr().out.splitlines() if "token: " in line
    )
    assert users.login("dina", "correct horse battery", "s" * 64, NOW) is not None
    assert users.authenticate(token) is not None  # the token made at the same time still works
    monkeypatch.setattr("sys.stdin", io.StringIO("another long password\n"))
    assert main(["user", "passwd", "dina", "--db", db]) == 0
    assert users.login("dina", "correct horse battery", "t" * 64, NOW) is None
    assert users.login("dina", "another long password", "t" * 64, NOW) is not None


def test_a_password_that_is_too_short_or_a_user_that_is_not_there_is_refused(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import io

    db = str(tmp_path / "flowstat.db")
    main(["user", "add", "dina", "--role", "designer", "--db", db])
    capsys.readouterr()
    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    assert main(["user", "passwd", "dina", "--db", db]) != 0
    assert "characters" in capsys.readouterr().err
    monkeypatch.setattr("sys.stdin", io.StringIO("a long enough password\n"))
    assert main(["user", "passwd", "ghost", "--db", db]) != 0
    assert "no user 'ghost'" in capsys.readouterr().err
