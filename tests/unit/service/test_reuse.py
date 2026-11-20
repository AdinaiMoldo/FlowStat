"""US-58: the key under which the result of a step that only computes is kept."""

from __future__ import annotations

from pathlib import Path

from flowstat.actions.registry import default_registry
from flowstat.engine.runner import Error, Ok
from flowstat.engine.scheduler import Start
from flowstat.repo.db import open_database
from flowstat.repo.reuse import ReuseRepo
from flowstat.service.reuse import key_of, reusing

REGISTRY = default_registry()


def key(action: str, args: dict[str, object], data: Path, **more: object) -> str | None:
    spec = REGISTRY.spec(action)
    assert spec is not None
    return key_of(spec, Start("s", action, args, **more), data)  # type: ignore[arg-type]


def test_the_same_arguments_give_the_same_key_whatever_their_order(tmp_path: Path) -> None:
    rows = [{"code": "I01", "name": "Population", "value": 1}]
    one = key("statguard.validate", {"rows": rows, "rules": "basic"}, tmp_path)
    two = key("statguard.validate", {"rules": "basic", "rows": rows}, tmp_path)
    other = key("statguard.validate", {"rows": rows, "rules": "regional-indicators-v1"}, tmp_path)
    assert one is not None and one == two and one != other
    assert key("stats.aggregate", {"parts": [rows]}, tmp_path) != one  # another action


def test_the_content_of_the_file_that_is_read_is_part_of_the_key(tmp_path: Path) -> None:
    (tmp_path / "OSH").mkdir()
    form = tmp_path / "OSH" / "2026Q3.xlsx"
    form.write_bytes(b"first")
    first = key("xlsx.read", {"path": "OSH/2026Q3.xlsx"}, tmp_path)
    assert first is not None and first == key("xlsx.read", {"path": "OSH/2026Q3.xlsx"}, tmp_path)
    form.write_bytes(b"corrected")
    assert key("xlsx.read", {"path": "OSH/2026Q3.xlsx"}, tmp_path) != first


def test_a_call_that_must_be_made_has_no_key(tmp_path: Path) -> None:
    assert key("db.write", {"rows": []}, tmp_path) is None  # an effect
    assert key("email.send", {}, tmp_path) is None
    assert key("approval.wait", {"role": "chief_statistician"}, tmp_path) is None  # a person
    assert key("xlsx.read", {"path": "OSH/none.xlsx"}, tmp_path) is None  # no file: let it fail
    assert key("xlsx.read", {"path": "../outside.xlsx"}, tmp_path) is None
    assert key("stats.aggregate", {"parts": [object()]}, tmp_path) is None  # cannot be written
    assert key("stats.aggregate", {"parts": []}, tmp_path, compensation=True) is None


def test_no_action_with_an_effect_or_a_wait_is_declared_repeatable() -> None:
    for name in REGISTRY.names():
        spec = REGISTRY.spec(name)
        assert spec is not None
        assert not (spec.repeatable and (spec.effect or spec.waits)), name


def test_a_result_is_taken_from_an_earlier_run_and_a_failure_is_never_kept(tmp_path: Path) -> None:
    db = open_database(str(tmp_path / "t.db"))
    calls: list[str] = []

    def execute(command: Start) -> Ok | Error:
        calls.append(command.step)
        if command.args["parts"] == ["bad"]:
            return Error("E-ACT-999", "no")
        return Ok({"rows": [], "regions": 1})

    def wrapped(run: str):  # type: ignore[no-untyped-def]
        return reusing(execute, REGISTRY.spec, db, run, tmp_path, lambda: "t")

    good = Start("total", "stats.aggregate", {"parts": [[]]})
    bad = Start("total", "stats.aggregate", {"parts": ["bad"]})
    assert isinstance(wrapped("r1")(good), Ok) and calls == ["total"]
    assert isinstance(wrapped("r1")(good), Ok) and calls == ["total", "total"]  # the same run
    assert wrapped("r2")(good) == Ok({"rows": [], "regions": 1}) and len(calls) == 2  # taken
    assert isinstance(wrapped("r2")(bad), Error) and isinstance(wrapped("r3")(bad), Error)
    assert len(calls) == 4  # a failure is computed every time
    uses = {u.step: (u.reused, u.from_run) for u in ReuseRepo(db).uses("r2")}
    assert uses == {"total": (False, None)}  # the last thing that happened to the step in r2
    db.close()
