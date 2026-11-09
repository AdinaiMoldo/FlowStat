"""A service ships its runs to the core: all of them, once, whatever goes wrong (US-31)."""

from __future__ import annotations

import sqlite3

import pytest

from flowstat.model.events import Event, EventKind, event_from_dict
from flowstat.repo.db import open_database
from flowstat.repo.deployments import Deployment, DeploymentStore
from flowstat.repo.outbox import OutboxStore
from flowstat.repo.runs import RunStore
from flowstat.service.ingest import (
    IngestConflictError,
    IngestInvalidError,
    IngestService,
    UnknownKeyError,
    WrongDeploymentError,
    parse_batch,
)
from flowstat.service.outbox import Backoff, Outbox, Reply, SendFailed
from flowstat.service.users import hash_token

T = "2026-11-09T10:00:00Z"
KEY = "k1" * 32


class Core:
    """A core that is a second database behind the ingest service, as a ``send`` function."""

    def __init__(self, key: str = KEY) -> None:
        self.db = open_database(":memory:")
        DeploymentStore(self.db).add(
            Deployment(
                "d-1",
                "demo",
                4,
                "process",
                "running",
                "/b",
                T,
                T,
                ingest_key_sha256=hash_token(KEY),
            )
        )
        self.ingest = IngestService(self.db)
        self.key = key
        self.calls: list[dict[str, object]] = []
        self.down = False  # no answer at all
        self.lose_acks = 0  # store the batch, then fail before the answer arrives
        self.fail_after = -1  # calls that go through before the network fails (-1: never)

    def __call__(self, payload: dict[str, object]) -> Reply:
        self.calls.append(payload)
        if self.down or self.fail_after == 0:
            raise SendFailed("no route")
        if self.fail_after > 0:
            self.fail_after -= 1
        try:
            deployment = self.ingest.authenticate("d-1", self.key)
            last = self.ingest.accept(deployment, parse_batch(payload), T)
        except UnknownKeyError:
            return Reply(401)
        except WrongDeploymentError:
            return Reply(403)
        except IngestConflictError as error:
            body: dict[str, object] = {} if error.expected is None else {"expected": error.expected}
            return Reply(409, body)
        except IngestInvalidError:
            return Reply(422)
        if self.lose_acks:
            self.lose_acks -= 1
            raise SendFailed("the answer was lost")
        return Reply(200, {"stored_through": last})

    def events(self, run_id: str) -> list[Event]:
        return RunStore(self.db).events(run_id)


def service(runs: dict[str, int]) -> sqlite3.Connection:
    """A service database with runs of the given number of events."""
    db = open_database(":memory:")
    store = RunStore(db)
    for i, (run_id, n) in enumerate(runs.items()):
        store.create(run_id, "demo", 4, {"quarter": "2025Q1"}, f"2026-11-09T09:0{i}:00Z")
        store.append(
            run_id, [Event(s, EventKind.STEP_STARTED, float(s), step="a") for s in range(n)]
        )
    return db


def grow(db: sqlite3.Connection, run_id: str, start: int, n: int) -> None:
    RunStore(db).append(
        run_id,
        [Event(s, EventKind.STEP_STARTED, float(s), step="a") for s in range(start, start + n)],
    )


def test_every_run_reaches_the_core_with_its_whole_history() -> None:
    db, core = service({"r1": 5, "r2": 3}), Core()
    report = Outbox(db, core).ship_once()
    assert (report.events, report.runs, report.clean) == (8, 2, True)
    assert core.events("r1") == RunStore(db).events("r1")
    assert core.events("r2") == RunStore(db).events("r2")
    assert RunStore(core.db).get("r1").origin == "d-1"


def test_a_long_history_goes_in_batches_of_the_given_size() -> None:
    db, core = service({"r1": 25}), Core()
    Outbox(db, core, batch_size=10).ship_once()
    assert [len(c["events"]) for c in core.calls] == [10, 10, 5]  # type: ignore[arg-type]
    assert len(core.events("r1")) == 25


def test_a_second_round_with_nothing_new_sends_nothing() -> None:
    db, core = service({"r1": 4}), Core()
    outbox = Outbox(db, core)
    outbox.ship_once()
    before = len(core.calls)
    assert outbox.ship_once().events == 0
    assert len(core.calls) == before


def test_a_lost_acknowledgement_means_the_batch_is_sent_again_and_stored_once() -> None:
    db, core = service({"r1": 4}), Core()
    core.lose_acks = 1
    outbox = Outbox(db, core)
    first = outbox.ship_once()
    assert first.stopped is not None and OutboxStore(db).shipped_through("r1") == -1
    assert len(core.events("r1")) == 4  # the core has it, the service does not know
    second = outbox.ship_once()
    assert second.clean and OutboxStore(db).shipped_through("r1") == 3
    assert core.events("r1") == RunStore(db).events("r1")  # no duplicates


def test_a_network_failure_in_the_middle_keeps_what_was_acknowledged() -> None:
    db, core = service({"r1": 25}), Core()
    core.fail_after = 2
    outbox = Outbox(db, core, batch_size=10)
    report = outbox.ship_once()
    assert report.stopped is not None and report.events == 20
    assert OutboxStore(db).shipped_through("r1") == 19
    core.fail_after = -1
    assert outbox.ship_once().events == 5
    assert core.events("r1") == RunStore(db).events("r1")


def test_the_events_of_an_unfinished_run_follow_as_it_goes_on() -> None:
    db, core = service({"r1": 3}), Core()
    outbox = Outbox(db, core)
    outbox.ship_once()
    grow(db, "r1", 3, 2)
    assert outbox.ship_once().events == 2
    assert len(core.events("r1")) == 5


def test_a_core_that_lost_data_is_sent_what_it_lacks() -> None:
    db, core = service({"r1": 6}), Core()
    outbox = Outbox(db, core)
    outbox.ship_once()
    # the core is restored from an older copy: it has events 0..1 of the run only
    fresh = Core()
    fresh.ingest.runs.ingest(
        "r1",
        "demo",
        4,
        {"quarter": "2025Q1"},
        "2026-11-09T09:00:00Z",
        "d-1",
        RunStore(db).events("r1")[:2],
    )
    grow(db, "r1", 6, 1)
    report = Outbox(db, fresh).ship_once()  # the service still believes 5 were acknowledged
    assert report.clean
    assert fresh.events("r1") == RunStore(db).events("r1")


def test_a_core_that_has_no_trace_of_the_run_gets_it_from_the_start() -> None:
    db, core = service({"r1": 4}), Core()
    OutboxStore(db).set("r1", 3)  # the service believes everything is delivered
    grow(db, "r1", 4, 1)
    report = Outbox(db, core).ship_once()
    assert report.clean and core.events("r1") == RunStore(db).events("r1")


def test_a_conflicting_event_skips_that_run_and_ships_the_others() -> None:
    db, core = service({"r1": 3, "r2": 3}), Core()
    core.ingest.runs.ingest(
        "r1",
        "demo",
        4,
        {"quarter": "2025Q1"},
        "2026-11-09T09:00:00Z",
        "d-1",
        [Event(0, EventKind.STEP_STARTED, 0.0, step="other")],
    )
    report = Outbox(db, core).ship_once()
    assert report.skipped == ("r1",) and report.runs == 1 and report.stopped is None
    assert len(core.events("r2")) == 3


def test_a_refused_key_ends_the_round_and_nothing_is_marked_shipped() -> None:
    db, core = service({"r1": 3}), Core(key="x" * 64)
    report = Outbox(db, core).ship_once()
    assert report.stopped is not None and "401" in report.stopped
    assert OutboxStore(db).shipped_through("r1") == -1


def test_a_core_that_is_down_is_reported_not_raised() -> None:
    db, core = service({"r1": 3}), Core()
    core.down = True
    report = Outbox(db, core).ship_once()
    assert report.stopped is not None and "not reachable" in report.stopped
    assert report.events == 0


def test_an_error_of_the_core_ends_the_round() -> None:
    db = service({"r1": 3})
    report = Outbox(db, lambda _p: Reply(503)).ship_once()
    assert report.stopped is not None and "503" in report.stopped


def test_a_core_that_keeps_asking_for_the_same_event_does_not_loop() -> None:
    db = service({"r1": 3})
    calls: list[int] = []

    def stubborn(payload: dict[str, object]) -> Reply:
        calls.append(1)
        return Reply(409, {"expected": 1})

    report = Outbox(db, stubborn).ship_once()
    assert report.skipped == ("r1",) and len(calls) == 2


def test_the_wait_between_failed_rounds_doubles_up_to_the_cap_and_resets() -> None:
    wait = Backoff()
    assert [wait.next() for _ in range(7)] == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
    wait.reset()
    assert wait.next() == 1.0


def test_the_events_sent_are_the_ones_of_the_log() -> None:
    db, core = service({"r1": 2}), Core()
    Outbox(db, core).ship_once()
    sent = core.calls[0]["events"]
    assert [event_from_dict(e) for e in sent] == RunStore(db).events("r1")  # type: ignore[attr-defined]


@pytest.mark.parametrize("size", [1, 2, 7])
def test_any_batch_size_ends_with_the_same_history(size: int) -> None:
    db, core = service({"r1": 9}), Core()
    Outbox(db, core, batch_size=size).ship_once()
    assert core.events("r1") == RunStore(db).events("r1")
