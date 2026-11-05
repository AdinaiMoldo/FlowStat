"""Tests of the registry: arguments, the declared output schema, unknown actions."""

from __future__ import annotations

from pathlib import Path

from flowstat.actions.base import ActionContext, ActionError, ActionSpec
from flowstat.actions.registry import Registry, default_registry
from flowstat.engine.runner import Error, Ok, Pause
from flowstat.engine.scheduler import Start
from tests.unit.actions.helpers import GOOD_ROWS, write_form


def ctx(tmp_path: Path) -> ActionContext:
    return ActionContext("run-1", "s", tmp_path / "data", str(tmp_path / "target.db"))


def spec(handler: object, output: dict[str, type] | None = None) -> ActionSpec:
    return ActionSpec("t.go", {"n": int}, output, handler)  # type: ignore[arg-type]


def test_known_names_feed_the_compiler() -> None:
    names = default_registry().names()
    assert {
        "xlsx.read",
        "statguard.validate",
        "stats.aggregate",
        "approval.wait",
        "db.write",
        "db.delete_batch",
        "email.send",
    } <= names


def test_the_example_workflow_uses_only_known_actions() -> None:
    from tests.unit.engine.helpers import EXAMPLE

    used = {line.split(":", 1)[1].strip() for line in EXAMPLE.splitlines() if "action:" in line}
    assert used - {"x.y"} <= default_registry().names()


def test_an_unknown_action_fails_the_step(tmp_path: Path) -> None:
    out = default_registry().executor(ctx(tmp_path))(Start("s", "nope.do", {}))
    assert isinstance(out, Error) and out.code == "E-ACT-001" and "nope.do" in out.message


def test_a_missing_or_mistyped_argument_fails_the_step(tmp_path: Path) -> None:
    run = Registry([spec(lambda a, c: {})]).executor(ctx(tmp_path))
    missing = run(Start("s", "t.go", {}))
    wrong = run(Start("s", "t.go", {"n": "five"}))
    assert isinstance(missing, Error) and missing.code == "E-ACT-009" and "'n'" in missing.message
    assert isinstance(wrong, Error) and wrong.code == "E-ACT-009"


def test_an_output_that_breaks_the_declared_schema_fails_the_step(tmp_path: Path) -> None:
    run = Registry([spec(lambda a, c: {"x": "text"}, {"x": int})]).executor(ctx(tmp_path))
    out = run(Start("s", "t.go", {"n": 1}))
    assert isinstance(out, Error) and out.code == "E-ACT-004" and "'x'" in out.message
    missing = Registry([spec(lambda a, c: {}, {"x": int})]).executor(ctx(tmp_path))
    assert isinstance(missing(Start("s", "t.go", {"n": 1})), Error)


def test_a_boolean_does_not_satisfy_an_integer_field(tmp_path: Path) -> None:
    run = Registry([spec(lambda a, c: {"x": True}, {"x": int})]).executor(ctx(tmp_path))
    assert isinstance(run(Start("s", "t.go", {"n": 1})), Error)


def test_an_action_error_becomes_an_error_outcome(tmp_path: Path) -> None:
    def fail(args: object, context: object) -> dict[str, object]:
        raise ActionError("E-ACT-002", "cannot read BIS/2025Q1.xlsx")

    out = Registry([spec(fail)]).executor(ctx(tmp_path))(Start("s", "t.go", {"n": 1}))
    assert out == Error("E-ACT-002", "cannot read BIS/2025Q1.xlsx")


def test_the_step_name_reaches_the_action(tmp_path: Path) -> None:
    seen: list[str] = []

    def note(args: object, context: ActionContext) -> dict[str, object]:
        seen.append(context.step)
        return {}

    Registry([spec(note)]).executor(ctx(tmp_path))(Start("load[BIS]", "t.go", {"n": 1}))
    assert seen == ["load[BIS]"]


def test_approval_pauses_the_step(tmp_path: Path) -> None:
    out = default_registry().executor(ctx(tmp_path))(
        Start("approve", "approval.wait", {"role": "chief_statistician"})
    )
    assert isinstance(out, Pause)


def test_reading_a_form_through_the_registry_gives_its_declared_schema(tmp_path: Path) -> None:
    write_form(tmp_path / "data" / "BIS" / "2025Q1.xlsx", GOOD_ROWS)
    out = default_registry().executor(ctx(tmp_path))(
        Start("load[BIS]", "xlsx.read", {"path": "BIS/2025Q1.xlsx"})
    )
    assert isinstance(out, Ok) and set(out.output) == {"rows", "count"}


def test_a_transient_action_error_is_marked_transient(tmp_path: Path) -> None:
    def lost(args: object, context: object) -> dict[str, object]:
        raise ActionError("E-ACT-008", "connection lost", transient=True)

    out = Registry([spec(lost)]).executor(ctx(tmp_path))(Start("s", "t.go", {"n": 1}))
    assert out == Error("E-ACT-008", "connection lost", transient=True)


def test_an_ordinary_action_error_is_permanent(tmp_path: Path) -> None:
    def bad(args: object, context: object) -> dict[str, object]:
        raise ActionError("E-ACT-002", "cannot read x")

    out = Registry([spec(bad)]).executor(ctx(tmp_path))(Start("s", "t.go", {"n": 1}))
    assert isinstance(out, Error) and out.transient is False


def test_the_idempotency_key_of_an_attempt_reaches_the_action(tmp_path: Path) -> None:
    keys: list[str] = []

    def note(args: object, context: ActionContext) -> dict[str, object]:
        keys.append(context.key)
        return {}

    run = Registry([spec(note)]).executor(ctx(tmp_path))
    run(Start("load[BIS]", "t.go", {"n": 1}))
    run(Start("load[BIS]", "t.go", {"n": 1}, attempt=3))
    assert keys == ["run-1/load[BIS]/1", "run-1/load[BIS]/3"]


def test_the_key_of_a_compensation_differs_from_the_key_of_the_step(tmp_path: Path) -> None:
    keys: list[str] = []

    def note(args: object, context: ActionContext) -> dict[str, object]:
        keys.append(context.key)
        return {}

    run = Registry([spec(note)]).executor(ctx(tmp_path))
    run(Start("a", "t.go", {"n": 1}))
    run(Start("a", "t.go", {"n": 1}, compensation=True))
    assert keys == ["run-1/a/1", "run-1/a/compensate/1"]


def test_only_actions_that_ignore_the_key_are_not_idempotent() -> None:
    registry = default_registry()

    assert registry.is_idempotent("db.write") and registry.is_idempotent("db.delete_batch")
    assert registry.is_idempotent("xlsx.read") and registry.is_idempotent("statguard.validate")
    assert registry.is_idempotent("stats.aggregate") and registry.is_idempotent("approval.wait")
    assert not registry.is_idempotent("email.send")


def test_an_unknown_action_is_not_idempotent() -> None:
    assert not default_registry().is_idempotent("nope.nothing")
