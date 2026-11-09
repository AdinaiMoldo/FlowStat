"""US-33: As an operator, I move a running run to a new version without losing its history.

Given a run that waits for an approval, when it is moved to a deployed service of the new version,
then the service has the whole history, the core refuses commands for the run and names the
service, the approval given to the service finishes the run, and the core shows it finished.
The other way: a run of the service moves into the core and finishes there. The service is a real
process and the core a real server, so the control key, the fence and the HTTP are the ones that
run in production.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx2

from flowstat.model.events import EventKind
from tests.acceptance.test_us30 import QUARTER, _wait_for, decide
from tests.acceptance.test_us31 import (
    ANSWER,
    core_events,
    core_listening,
    core_shows,
    deploy,
    free_port,
    make_core,
    service_events,
    stop,
    until,
)
from tests.unit.api.helpers import NAME, Env


def start_at_core(env: Env, version: int) -> str:
    started = env.call(
        "operator", "POST", f"/workflows/{NAME}/runs", json=QUARTER | {"version": version}
    )
    assert started.status_code == 202, started.text
    run_id: str = started.json()["run_id"]
    assert env.call("operator", "GET", f"/runs/{run_id}").json()["status"] == "WAITING"
    return run_id


def migrate(env: Env, run_id: str, to: int, owner: str) -> httpx2.Response:
    return env.call("operator", "POST", f"/runs/{run_id}/migrate", json={"to": to, "owner": owner})


def kinds(events: list[Any]) -> list[EventKind]:
    return [e.kind for e in events]


def test_a_waiting_run_moves_into_a_real_service_and_finishes_there(tmp_path: Path) -> None:
    port = free_port()
    env = make_core(tmp_path, port)
    with core_listening(env, port):
        row = deploy(env)
        try:
            run_id = start_at_core(env, 1)
            before = core_events(env, run_id)

            moved = migrate(env, run_id, 4, row["id"])
            assert moved.status_code == 200, moved.text
            assert moved.json() == {
                "run_id": run_id, "from": 1, "to": 4, "owner": row["id"], "epoch": 1,
                "status": "WAITING",
            }  # fmt: skip

            # the service has the whole history, then the marker of the move
            held = service_events(env, row, run_id)
            assert held[: len(before)] == before
            assert kinds(held[len(before) :]) == [EventKind.RUN_MIGRATED]

            # the core says where the run is and refuses commands for it
            [listed] = env.call("operator", "GET", f"/runs?deployment={row['id']}").json()
            assert (listed["run_id"], listed["version"]) == (run_id, 4)
            refused = env.call("operator", "POST", f"/runs/{run_id}/decisions", json=ANSWER)
            assert refused.status_code == 409
            assert refused.json()["errors"][0]["code"] == "E-MIG-010"
            assert row["id"] in refused.json()["errors"][0]["message"]

            # the approval is given to the service, which finishes the run
            key = {"Authorization": f"Bearer {row['api_key']}"}
            _wait_for(row["endpoint"], key, run_id, "WAITING")
            title = {"Authorization": f"Bearer {row['title_keys']['chief_statistician']}"}
            assert (
                decide(f"{row['endpoint']}/runs/{run_id}/decisions", ANSWER, title).status_code
                == 202
            )
            _wait_for(row["endpoint"], key, run_id, "FINISHED")

            # and its outbox brings the end of the history to the core
            until(lambda: core_shows(env, row["id"], run_id, "FINISHED"), "the core was not told")
            until(
                lambda: len(core_events(env, run_id)) == len(service_events(env, row, run_id)),
                "the core holds another history than the service",
            )
            assert core_events(env, run_id) == service_events(env, row, run_id)
        finally:
            stop(env, row)


def test_a_run_of_a_real_service_moves_into_the_core_and_finishes_there(tmp_path: Path) -> None:
    port = free_port()
    env = make_core(tmp_path, port)
    with core_listening(env, port):
        row = deploy(env)
        try:
            key = {"Authorization": f"Bearer {row['api_key']}"}
            started = httpx2.post(row["endpoint"] + "/runs", json=QUARTER, headers=key, timeout=30)
            run_id: str = started.json()["run_id"]
            _wait_for(row["endpoint"], key, run_id, "WAITING")
            until(lambda: core_shows(env, row["id"], run_id, "WAITING"), "the core was not told")

            moved = migrate(env, run_id, 3, "core")  # v3 is the same text as v4
            assert moved.status_code == 200, moved.text
            assert (moved.json()["from"], moved.json()["epoch"]) == (4, 1)

            [listed] = env.call("operator", "GET", "/runs").json()
            assert (listed["origin"], listed["version"]) == (None, 3)
            title = {"Authorization": f"Bearer {row['title_keys']['chief_statistician']}"}
            late = decide(f"{row['endpoint']}/runs/{run_id}/decisions", ANSWER, title)
            assert late.status_code == 409 and late.json()["errors"][0]["code"] == "E-MIG-010"

            assert (
                env.call("operator", "POST", f"/runs/{run_id}/decisions", json=ANSWER).status_code
                == 202
            )
            report = env.call("operator", "GET", f"/runs/{run_id}").json()
            assert (report["version"], report["status"], report["outcome"]) == (
                3, "FINISHED", "SUCCEEDED",
            )  # fmt: skip
            assert kinds(core_events(env, run_id)).count(EventKind.RUN_MIGRATED) == 1

            # the service holds the run as left, and does not ship it again
            assert (
                service_events(env, row, run_id)
                == core_events(env, run_id)[: len(service_events(env, row, run_id))]
            )
            assert kinds(service_events(env, row, run_id))[-1] is not EventKind.RUN_FINISHED
        finally:
            stop(env, row)
