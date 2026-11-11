"""A process that stops at every event of a run and starts again (NFR-02, UR-03, US-36).

The nine-region example is run to its approval and answered, which gives its whole history. For
each cut of that history (the moment the process stopped) a new core is made on a database that
holds the history up to the cut; the start of the core is the only thing done. Then, if the run
waits for the approval, the approval is answered. Afterwards:

* the history up to the cut is untouched and the one after it continues without a gap;
* the run ends as the uninterrupted run did: same outcome, every step in the same state with the
  same output, unless a step that is not idempotent was in flight, which is then uncertain and
  never run again by itself (the only person who can tell is the one who resolves it);
* nothing is done for a cut at which the run was waiting or had finished.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from flowstat.api.app import create_app
from flowstat.model.events import Event, EventKind
from flowstat.repo.db import connect
from flowstat.repo.runs import RunStore
from tests.unit.actions.helpers import write_all_forms
from tests.unit.api.helpers import EXAMPLE, NAME, Env, make_env

RUN = "r-0001"
INPUTS = {"quarter": "2025Q1"}


def publish(env: Env) -> None:
    env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    assert env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish").status_code == 200
    write_all_forms(env.config.data_dir, "2025Q1")


def answer(env: Env, decision: str) -> None:
    response = env.call(
        "operator",
        "POST",
        f"/runs/{RUN}/decisions",
        json={"step": "approve", "decision": decision},
    )
    assert response.status_code == 202, response.text


def events(env: Env) -> list[Event]:
    db = connect(env.config.db_path)
    try:
        return RunStore(db).events(RUN)
    finally:
        db.close()


def shape(env: Env) -> dict[str, Any]:
    report: dict[str, Any] = env.call("operator", "GET", f"/runs/{RUN}").json()
    return {
        "status": report["status"],
        "outcome": report["outcome"],
        "steps": {s["step"]: (s["state"], s["output"]) for s in report["steps"]},
    }


@pytest.fixture(scope="module", params=["approved", "rejected"])
def whole(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> tuple[str, list[Event], dict[str, Any]]:
    """The whole history of a run that was answered, and how the run ended."""
    decision: str = request.param
    env = make_env(tmp_path_factory.mktemp(decision))
    publish(env)
    started = env.call("operator", "POST", f"/workflows/{NAME}/runs", json={"inputs": INPUTS})
    assert started.status_code == 202
    answer(env, decision)
    done = shape(env)
    assert done["status"] == "FINISHED"
    return decision, events(env), done


def restart(config_of: Env, history: list[Event]) -> Env:
    """A core on a database that holds ``history``: the one thing done is to start it."""
    db = connect(config_of.config.db_path)
    store = RunStore(db)
    store.create(RUN, NAME, 1, dict(INPUTS), "2026-11-11T10:00:00Z")
    if history:
        store.append(RUN, history)
    db.close()
    config = replace(config_of.config)
    return Env(TestClient(create_app(config)), config)


def test_the_run_has_a_history_worth_cutting(
    whole: tuple[str, list[Event], dict[str, Any]],
) -> None:
    _, history, _ = whole
    assert len(history) >= 40


def test_every_cut_of_the_history_goes_on_to_the_same_end(
    whole: tuple[str, list[Event], dict[str, Any]], tmp_path: Path
) -> None:
    decision, history, done = whole
    uncertain = 0
    for cut in range(0, len(history)):
        (tmp_path / f"cut-{cut}").mkdir()
        env = make_env(tmp_path / f"cut-{cut}")
        publish(env)
        env = restart(env, history[:cut])
        stored = events(env)
        assert stored[:cut] == history[:cut], f"cut {cut}: the history before the cut changed"
        assert [e.seq for e in stored] == list(range(len(stored))), f"cut {cut}: a gap"
        now = shape(env)
        if now["status"] == "WAITING" and now["steps"]["approve"][0] == "WAITING":
            answer(env, decision)
            now = shape(env)
        if now["status"] == "FINISHED":
            assert now == done, f"cut {cut}: another end"
        else:  # a step that may have taken effect and is not idempotent: only a person resolves it
            assert now["status"] == "WAITING", f"cut {cut}: {now['status']}"
            assert any(state == "UNCERTAIN" for state, _ in now["steps"].values()), f"cut {cut}"
            uncertain += 1
    if decision == "rejected":  # the mail is not idempotent: a cut while it was sent shows it
        assert uncertain >= 1
    else:
        assert uncertain == 0  # every step on the way to the publication is idempotent


def test_a_run_that_waits_is_not_touched_by_a_start(
    whole: tuple[str, list[Event], dict[str, Any]], tmp_path: Path
) -> None:
    _, history, _ = whole
    waiting = next(
        i + 1
        for i, e in enumerate(history)
        if e.step == "approve" and e.kind.value == "STEP_WAITING"
    )
    (tmp_path / "waiting").mkdir()
    env = make_env(tmp_path / "waiting")
    publish(env)
    env = restart(env, history[:waiting])
    assert events(env) == history[:waiting]


def test_a_run_that_was_stored_and_never_started_is_started_and_not_resumed(
    whole: tuple[str, list[Event], dict[str, Any]], tmp_path: Path
) -> None:
    _, history, _ = whole
    (tmp_path / "fresh").mkdir()
    env = make_env(tmp_path / "fresh")
    publish(env)
    env = restart(env, [])
    kinds = [e.kind for e in events(env)]
    assert kinds[0] is history[0].kind and EventKind.RUN_RESUMED not in kinds
