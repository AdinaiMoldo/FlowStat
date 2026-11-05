"""Acceptance tests of US-15 (a run survives a crash) and US-16 (uncertain steps).

The crash is made the way it happens: the engine process stops between two events, here by an
exception in the journal of the driver, with the thread pool left to finish what it was doing. So
an action may have had its effect while its result was never stored, which is the case that
matters. The run is then taken up again with the command line.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from flowstat.actions.base import ActionContext
from flowstat.actions.registry import default_registry
from flowstat.engine.runner import run_threaded
from flowstat.model.events import Event, EventKind
from flowstat.model.states import StepState
from flowstat.service.cli import main, open_services
from tests.unit.actions.helpers import write_form

K = EventKind

WRITE_THEN_CHECK = """workflow: crashy
version: 1
steps:
  read:
    action: xlsx.read
    with: {path: "BIS/2025Q1.xlsx"}
  write:
    action: db.write
    needs: [read]
    with: {rows: "${read.rows}"}
  check:
    action: statguard.validate
    needs: [write]
    with: {rows: "${read.rows}"}
goals: {any_of: [check]}
"""

READ_THEN_MAIL = """workflow: mailer
version: 1
steps:
  read:
    action: xlsx.read
    with: {path: "BIS/2025Q1.xlsx"}
  notify:
    action: email.send
    needs: [read]
goals: {any_of: [notify]}
"""


class Killed(Exception):
    """The engine process died."""


class Setup:
    def __init__(self, tmp_path: Path, text: str, name: str) -> None:
        self.name = name
        self.db = str(tmp_path / "flowstat.db")
        self.data = tmp_path / "forms"
        self.target = str(tmp_path / "target.db")
        write_form(self.data / "BIS" / "2025Q1.xlsx", [("I01", "Population", 100)])
        workflow = tmp_path / f"{name}.yaml"
        workflow.write_text(text, encoding="utf8")
        assert main(["publish", str(workflow), "--db", self.db]) == 0

    @property
    def where(self) -> list[str]:
        return ["--db", self.db, "--data", str(self.data), "--target", self.target]

    def crash(self, run_id: str, dies: Callable[[list[Event]], bool]) -> None:
        """Run until the journal is asked to store events for which ``dies`` is true; stop there
        without storing them (the actions that were started go on and finish)."""
        _, runs = open_services(self.db)
        runs.start(run_id, self.name, 1, {}, "2026-11-05T10:00:00Z")
        launch = runs.launch(run_id, 2)

        def journal() -> None:
            if dies(launch.scheduler.events):
                raise Killed
            launch.log.commit(launch.scheduler.events)

        base = ActionContext(run_id, "", self.data, self.target)
        with pytest.raises(Killed):
            run_threaded(
                launch.scheduler,
                default_registry().executor(base),
                time.time,
                time.sleep,
                journal=journal,
            )

    def events(self, run_id: str) -> list[Event]:
        return open_services(self.db)[1].store.events(run_id)

    def rows(self, table: str) -> int:
        db = sqlite3.connect(self.target)
        try:
            return int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except sqlite3.OperationalError:
            return 0
        finally:
            db.close()


def finished(step: str) -> Callable[[list[Event]], bool]:
    """True when the events contain the end of this step: its result is about to be stored."""
    return lambda events: any(e.kind is K.STEP_FINISHED and e.step == step for e in events)


# -- US-15 -------------------------------------------------------------------------------------
def test_us15_a_finished_step_is_not_run_again_and_the_running_one_runs_once_more_and_finishes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given the engine was killed while idempotent write was RUNNING (read had succeeded); the
    # rows of write are in the target database but the end of the step was never stored
    s = Setup(tmp_path, WRITE_THEN_CHECK, "crashy")
    s.crash("r1", finished("write"))
    assert s.rows("published") == 1
    assert not any(e.kind is K.STEP_FINISHED and e.step == "write" for e in s.events("r1"))
    (s.data / "BIS" / "2025Q1.xlsx").unlink()  # read would fail now: it must not be run again
    capsys.readouterr()

    # When it restarts
    code = main(["resume", "r1", *s.where])

    # Then read is not run again, write runs once more with the same key, and the run completes
    out = capsys.readouterr().out
    assert code == 0, out
    events = s.events("r1")
    assert [e.step for e in events if e.kind is K.STEP_STARTED].count("read") == 1
    assert any(e.kind is K.RUN_RESUMED for e in events)
    assert s.rows("published") == 1  # the same key, so the effect exists once
    report = {line.split()[0]: line.split() for line in out.splitlines()[2:]}
    assert report["read"][1] == "SUCCEEDED" and report["read"][-1] == "1"
    assert report["write"][1] == "SUCCEEDED" and report["write"][-1] == "1"
    assert report["check"][1] == "SUCCEEDED"


def test_us15_a_run_that_has_no_event_of_its_own_yet_is_started_by_resume(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    s = Setup(tmp_path, WRITE_THEN_CHECK, "crashy")
    _, runs = open_services(s.db)
    runs.start("r1", "crashy", 1, {}, "2026-11-05T10:00:00Z")  # created, nothing else stored
    capsys.readouterr()

    code = main(["resume", "r1", *s.where])

    assert code == 0 and "SUCCEEDED" in capsys.readouterr().out


def test_us15_resuming_a_finished_run_changes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    s = Setup(tmp_path, WRITE_THEN_CHECK, "crashy")
    assert main(["run", "crashy", *s.where, "--run-id", "r1"]) == 0
    before = s.events("r1")
    capsys.readouterr()

    code = main(["resume", "r1", *s.where])

    assert code == 0 and s.events("r1") == before


def test_us15_the_history_is_stored_while_the_run_goes_on(tmp_path: Path) -> None:
    # a crash at the end of write: everything before it is in the store (write-ahead)
    s = Setup(tmp_path, WRITE_THEN_CHECK, "crashy")
    s.crash("r1", finished("write"))

    kinds = [(e.kind, e.step) for e in s.events("r1")]

    assert kinds == [
        (K.RUN_STARTED, None),
        (K.STEP_STARTED, "read"),
        (K.STEP_FINISHED, "read"),
        (K.STEP_STARTED, "write"),
    ]


def test_us15_an_unknown_run_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    s = Setup(tmp_path, WRITE_THEN_CHECK, "crashy")

    code = main(["resume", "nope", *s.where])

    assert code == 2 and "there is no run nope" in capsys.readouterr().err


# -- US-16 -------------------------------------------------------------------------------------
def crashed_mailer(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Setup:
    s = Setup(tmp_path, READ_THEN_MAIL, "mailer")
    s.crash("m1", finished("notify"))  # the mail was queued, the engine died before it knew
    assert s.rows("outbox") == 1
    capsys.readouterr()
    return s


def test_us16_a_step_that_is_not_idempotent_becomes_uncertain_and_nothing_is_run_again(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given the engine was killed while the non-idempotent email.send was RUNNING
    s = crashed_mailer(tmp_path, capsys)

    # When it restarts
    code = main(["resume", "m1", *s.where])

    # Then the step is UNCERTAIN and nothing is run again automatically
    out = capsys.readouterr().out
    assert code == 3, out  # the run waits for a person
    assert "UNCERTAIN" in out and "E-REC-001" in out and "WAITING" in out
    states = {e.step: e for e in s.events("m1") if e.kind is K.STEP_UNCERTAIN}
    assert states["notify"].state is StepState.RUNNING
    assert [e.step for e in s.events("m1") if e.kind is K.STEP_STARTED].count("notify") == 1


def test_us16_the_operator_marks_the_step_done(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    s = crashed_mailer(tmp_path, capsys)
    assert main(["resume", "m1", *s.where]) == 3
    capsys.readouterr()

    code = main(["resolve", "m1", "notify", "--done", "--output", '{"queued": 1}', *s.where])

    out = capsys.readouterr().out
    assert code == 0 and "SUCCEEDED" in out
    resolved = [e for e in s.events("m1") if e.kind is K.STEP_RESOLVED]
    assert [(e.step, e.decision) for e in resolved] == [("notify", "done")]
    assert s.rows("outbox") == 1  # not sent again


def test_us16_the_operator_asks_for_a_retry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    s = crashed_mailer(tmp_path, capsys)
    assert main(["resume", "m1", *s.where]) == 3
    capsys.readouterr()

    code = main(["resolve", "m1", "notify", "--retry", *s.where])

    out = capsys.readouterr().out
    assert code == 0 and "SUCCEEDED" in out
    notify = next(line.split() for line in out.splitlines() if line.startswith("notify"))
    assert notify[-1] == "2"  # a second attempt
    resolved = [e for e in s.events("m1") if e.kind is K.STEP_RESOLVED]
    assert [(e.decision, e.attempt) for e in resolved] == [("retry", 2)]


def test_us16_resolve_can_be_the_first_command_after_the_crash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    s = crashed_mailer(tmp_path, capsys)

    code = main(["resolve", "m1", "notify", "--done", *s.where])

    assert code == 0, capsys.readouterr().out
    kinds = [e.kind for e in s.events("m1")]
    assert K.STEP_UNCERTAIN in kinds and K.STEP_RESOLVED in kinds


def test_us16_a_step_that_is_not_uncertain_cannot_be_resolved(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    s = crashed_mailer(tmp_path, capsys)
    assert main(["resume", "m1", *s.where]) == 3
    capsys.readouterr()

    code = main(["resolve", "m1", "read", "--done", *s.where])

    assert code == 2
    assert "read is not uncertain" in capsys.readouterr().err


def test_us16_resolve_with_running_idempotent_steps_asks_for_resume_first(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    s = Setup(tmp_path, WRITE_THEN_CHECK, "crashy")
    s.crash("r1", finished("write"))  # write was running and is idempotent
    capsys.readouterr()

    code = main(["resolve", "r1", "write", "--done", *s.where])

    assert code == 2 and "resume" in capsys.readouterr().err
    assert not any(e.kind is K.STEP_RESOLVED for e in s.events("r1"))


def test_us16_the_decision_must_be_one_of_the_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    s = crashed_mailer(tmp_path, capsys)

    with pytest.raises(SystemExit) as stop:
        main(["resolve", "m1", "notify", *s.where])

    assert stop.value.code == 2


def test_us16_a_wrong_output_or_step_or_run_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    s = crashed_mailer(tmp_path, capsys)

    assert main(["resolve", "m1", "notify", "--done", "--output", "{oops", *s.where]) == 2
    assert main(["resolve", "m1", "notify", "--done", "--output", "[1]", *s.where]) == 2
    assert main(["resolve", "m1", "nowhere", "--done", *s.where]) == 2
    assert main(["resolve", "nope", "notify", "--done", *s.where]) == 2

    err = capsys.readouterr().err
    assert err.count("--output must be a JSON object") == 2
    assert "there is no step nowhere in run m1" in err and "there is no run nope" in err
    assert not any(e.kind is K.STEP_RESOLVED for e in s.events("m1"))


APPROVAL = """workflow: approval
version: 1
steps:
  read:
    action: xlsx.read
    with: {path: "BIS/2025Q1.xlsx"}
  approve:
    action: approval.wait
    needs: [read]
    with: {role: chief_statistician}
  write:
    action: db.write
    needs: [approve]
    when: "${approve.decision} == 'approved'"
    with: {rows: "${read.rows}"}
goals: {any_of: [write]}
"""


def test_us15_a_waiting_approval_is_still_waiting_after_a_restart_and_can_be_answered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # the approval takes days: the engine is stopped and restarted meanwhile
    s = Setup(tmp_path, APPROVAL, "approval")
    assert main(["run", "approval", *s.where, "--run-id", "a1"]) == 3
    capsys.readouterr()

    assert main(["resume", "a1", *s.where]) == 3  # still waiting; nothing was repeated
    assert [e.step for e in s.events("a1") if e.kind is K.STEP_STARTED].count("read") == 1
    code = main(["resume", "a1", *s.where, "--decision", "approved"])

    out = capsys.readouterr().out
    assert code == 0 and "SUCCEEDED" in out
    assert s.rows("published") == 1
