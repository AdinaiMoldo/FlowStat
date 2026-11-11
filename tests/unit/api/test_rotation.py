"""Replacing the control key or the ingest key of a deployed service (US-40).

The core is a real app; the service is a real app over its own database, reached in-process. A
transport that can stop (raise ``Crash``) before or after any call stands for a core that stopped
at that point; the next look (``recover``) must leave exactly one key working.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from flowstat.migration.handover import OwnerUnavailableError
from flowstat.repo.db import connect
from flowstat.repo.deployments import Deployment, DeploymentStore
from flowstat.service.ingest import IngestService, UnknownKeyError
from flowstat.service.rotation import KeyRotation, RotationRefusedError, ServiceAwayError
from flowstat.service.users import hash_token
from tests.unit.api.helpers import EXAMPLE, Env, make_env
from tests.unit.api.test_migrate import CONTROL, Service, World, deploy_service, publish

OLD_INGEST = "1a" * 32
NEW = "9f" * 32
DEPLOYMENT = "d-1"


class Crash(BaseException):
    """The core stopped here (a BaseException, so no handler of the code under test catches it)."""


@dataclass
class Wire:
    """The calls of the core to the service: they can be cut, counted and stopped."""

    world: World
    calls: list[tuple[str, str]] = field(default_factory=list)
    crash_before: int | None = None  # stop before the n-th call (1 based) is sent
    crash_after: int | None = (
        None  # stop after the n-th call was done and before its answer is used
    )
    away: bool = False

    def __call__(self, row: Deployment, key: str) -> Any:
        service = self.world.services[row.id]

        def call(method: str, path: str, body: dict[str, object] | None) -> Any:
            n = len(self.calls) + 1
            if self.away:
                raise OwnerUnavailableError("the service does not answer")
            if self.crash_before == n:
                raise Crash
            self.calls.append((method, path))
            answer = service.call(key, method, path, body)
            if self.crash_after == n:
                raise Crash
            return answer.status_code, answer.json()

        return call


@dataclass
class Setup:
    core: Env
    world: World
    service: Service
    wire: Wire

    def rotation(self) -> KeyRotation:
        db = connect(self.core.config.db_path)
        return KeyRotation(db, self.wire)

    def row(self) -> Deployment:
        db = connect(self.core.config.db_path)
        try:
            return DeploymentStore(db).get(DEPLOYMENT)
        finally:
            db.close()

    def accepted_by_service(self, key: str) -> bool:
        return self.service.call(key, "GET", "/keys").status_code == 200

    def accepted_by_core(self, key: str) -> bool:
        db = connect(self.core.config.db_path)
        try:
            IngestService(db).authenticate(DEPLOYMENT, key)
            return True
        except UnknownKeyError:
            return False
        finally:
            db.close()

    def ingest_of_service(self) -> str | None:
        return self.service.call(CONTROL, "GET", "/keys").json()["ingest_sha256"]


@pytest.fixture
def s(tmp_path: Path) -> Setup:
    core = make_env(tmp_path)
    world = World(core)
    wire = Wire(world)
    core.client.app.state.key_transport_for = wire  # type: ignore[attr-defined]
    core.client.app.state.transport_for = world.transport  # type: ignore[attr-defined]
    assert publish(core, EXAMPLE) == 1
    service = deploy_service(world, tmp_path, DEPLOYMENT, 1)
    service.client.app.state.env_ingest_key = OLD_INGEST  # type: ignore[attr-defined]
    db = connect(core.config.db_path)
    db.execute(
        "UPDATE deployments SET ingest_key_sha256 = ? WHERE id = ?",
        (hash_token(OLD_INGEST), DEPLOYMENT),
    )
    db.close()
    return Setup(core, world, service, wire)


# -- the control key ---------------------------------------------------------------------------
def test_the_new_control_key_works_and_the_old_one_is_refused_after_the_switch(s: Setup) -> None:
    assert s.accepted_by_service(CONTROL) and not s.accepted_by_service(NEW)
    s.rotation().rotate(DEPLOYMENT, "control", NEW, "T")
    assert s.row().control_key == NEW and s.row().control_key_next is None
    assert s.accepted_by_service(NEW) and not s.accepted_by_service(CONTROL)
    assert s.service.call(CONTROL, "GET", "/migration/x/ownership").status_code == 401


def test_a_service_that_was_rotated_is_still_moved_into_and_out_of_with_the_new_key(
    s: Setup,
) -> None:
    s.rotation().rotate(DEPLOYMENT, "control", NEW, "T")
    assert s.service.call(NEW, "GET", "/migration/nothing/ownership").status_code in (200, 404)


def test_in_the_middle_of_the_rotation_both_control_keys_work(s: Setup) -> None:
    s.wire.crash_after = 1  # the service was told the new key and the core stopped
    with pytest.raises(Crash):
        s.rotation().rotate(DEPLOYMENT, "control", NEW, "T")
    assert s.accepted_by_service(CONTROL) and s.accepted_by_service(NEW)
    assert s.row().control_key_next == NEW  # the core knows a rotation is under way


@pytest.mark.parametrize("mode", ["crash_before", "crash_after"])
@pytest.mark.parametrize("n", [1, 2])
def test_a_stop_at_any_call_of_the_control_rotation_leaves_exactly_one_key(
    s: Setup, mode: str, n: int
) -> None:
    setattr(s.wire, mode, n)
    with pytest.raises(Crash):
        s.rotation().rotate(DEPLOYMENT, "control", NEW, "T")
    setattr(s.wire, mode, None)
    outcome = KeyRotation(connect(s.core.config.db_path), s.wire).recover(DEPLOYMENT, "T2")
    old, new = s.accepted_by_service(CONTROL), s.accepted_by_service(NEW)
    assert old != new, (mode, n, outcome)  # exactly one
    row = s.row()
    assert row.control_key_next is None
    assert row.control_key == (NEW if new else CONTROL)  # the registry holds the one that works
    assert s.accepted_by_service(row.control_key or "")


def test_the_recovery_is_the_same_when_it_is_made_twice(s: Setup) -> None:
    s.wire.crash_after = 1
    with pytest.raises(Crash):
        s.rotation().rotate(DEPLOYMENT, "control", NEW, "T")
    s.wire.crash_after = None
    first = s.rotation().recover(DEPLOYMENT, "T")
    assert first == "completed"
    assert s.rotation().recover(DEPLOYMENT, "T") == "nothing"
    assert s.accepted_by_service(NEW) and not s.accepted_by_service(CONTROL)


def test_a_rotation_that_was_not_sent_is_taken_back(s: Setup) -> None:
    s.wire.crash_before = 1
    with pytest.raises(Crash):
        s.rotation().rotate(DEPLOYMENT, "control", NEW, "T")
    s.wire.crash_before = None
    assert s.rotation().recover(DEPLOYMENT, "T") == "taken back"
    assert s.row().control_key == CONTROL and s.accepted_by_service(CONTROL)


def test_a_service_that_does_not_answer_keeps_the_record_and_recovers_later(s: Setup) -> None:
    s.wire.crash_after = 1
    with pytest.raises(Crash):
        s.rotation().rotate(DEPLOYMENT, "control", NEW, "T")
    s.wire.crash_after = None
    s.wire.away = True
    assert s.rotation().recover(DEPLOYMENT, "T") == "away"
    assert s.row().control_key_next == NEW  # nothing was decided without an answer
    s.wire.away = False
    assert s.rotation().recover(DEPLOYMENT, "T") == "completed"


def test_the_next_key_may_not_be_announced_by_itself_or_by_the_next_key(s: Setup) -> None:
    assert (
        s.service.call(
            CONTROL, "POST", "/keys/control/prepare", {"sha256": hash_token(CONTROL)}
        ).status_code
        == 409
    )
    s.service.call(CONTROL, "POST", "/keys/control/prepare", {"sha256": hash_token(NEW)})
    again = s.service.call(NEW, "POST", "/keys/control/prepare", {"sha256": hash_token("a" * 40)})
    assert again.status_code == 409
    bad = s.service.call(CONTROL, "POST", "/keys/control/prepare", {"sha256": "nope"})
    assert bad.status_code == 409


def test_the_rollback_is_the_old_key_settling(s: Setup) -> None:
    s.service.call(CONTROL, "POST", "/keys/control/prepare", {"sha256": hash_token(NEW)})
    assert s.service.call(CONTROL, "POST", "/keys/control/settle").status_code == 200
    assert s.accepted_by_service(CONTROL) and not s.accepted_by_service(NEW)


def test_a_service_that_refuses_the_old_key_ends_the_rotation_and_changes_nothing(
    s: Setup,
) -> None:
    db = connect(s.core.config.db_path)
    db.execute("UPDATE deployments SET control_key = ?", ("wrong" * 10,))
    db.close()
    with pytest.raises(RotationRefusedError):
        s.rotation().rotate(DEPLOYMENT, "control", NEW, "T")
    assert s.row().control_key_next is None and s.accepted_by_service(CONTROL)


# -- the ingest key ----------------------------------------------------------------------------
def test_the_new_ingest_key_works_and_the_old_one_is_refused_after_the_switch(s: Setup) -> None:
    assert s.accepted_by_core(OLD_INGEST) and not s.accepted_by_core(NEW)
    s.rotation().rotate(DEPLOYMENT, "ingest", NEW, "T")
    assert s.accepted_by_core(NEW) and not s.accepted_by_core(OLD_INGEST)
    assert s.ingest_of_service() == hash_token(NEW)  # the service ships with the new key
    assert s.row().ingest_key_next_sha256 is None


def test_in_the_middle_of_the_ingest_rotation_the_core_takes_both_keys(s: Setup) -> None:
    s.wire.crash_before = 1  # the core wrote the new hash and stopped before the service knew
    with pytest.raises(Crash):
        s.rotation().rotate(DEPLOYMENT, "ingest", NEW, "T")
    assert s.accepted_by_core(OLD_INGEST) and s.accepted_by_core(NEW)


@pytest.mark.parametrize("mode", ["crash_before", "crash_after"])
def test_a_stop_at_the_call_of_the_ingest_rotation_leaves_exactly_one_key(
    s: Setup, mode: str
) -> None:
    setattr(s.wire, mode, 1)
    with pytest.raises(Crash):
        s.rotation().rotate(DEPLOYMENT, "ingest", NEW, "T")
    setattr(s.wire, mode, None)
    s.rotation().recover(DEPLOYMENT, "T2")
    old, new = s.accepted_by_core(OLD_INGEST), s.accepted_by_core(NEW)
    assert old != new, mode  # exactly one
    shipping = s.ingest_of_service()
    assert shipping == hash_token(NEW if new else OLD_INGEST)  # the service uses the one that works
    assert s.row().ingest_key_next_sha256 is None


def test_a_service_that_does_not_answer_cannot_be_given_a_key_and_the_record_stays(
    s: Setup,
) -> None:
    s.wire.away = True
    with pytest.raises(ServiceAwayError):
        s.rotation().rotate(DEPLOYMENT, "ingest", NEW, "T")
    assert s.row().ingest_key_next_sha256 == hash_token(NEW)
    s.wire.away = False
    assert s.rotation().recover(DEPLOYMENT, "T") == "taken back"  # the service never heard of it
    assert s.accepted_by_core(OLD_INGEST) and not s.accepted_by_core(NEW)


def test_the_ingest_key_of_a_service_survives_its_restart(s: Setup) -> None:
    from flowstat.service.keys import ServiceKeys

    s.rotation().rotate(DEPLOYMENT, "ingest", NEW, "T")
    db = connect(str(s.service.bundle / "service.db"))
    try:
        assert (
            ServiceKeys(db, OLD_INGEST).ingest_key() == NEW
        )  # the stored key beats the environment
    finally:
        db.close()


# -- refusals ----------------------------------------------------------------------------------
def test_only_a_running_service_that_answers_has_a_key_replaced(s: Setup) -> None:
    db = connect(s.core.config.db_path)
    db.execute("UPDATE deployments SET status = 'stopped'")
    db.close()
    with pytest.raises(RotationRefusedError, match="stopped"):
        s.rotation().rotate(DEPLOYMENT, "control", NEW, "T")
    db = connect(s.core.config.db_path)
    db.execute("UPDATE deployments SET status = 'running', answering = 0")
    db.close()
    with pytest.raises(RotationRefusedError, match="unreachable"):
        s.rotation().rotate(DEPLOYMENT, "ingest", NEW, "T")


def test_a_short_key_and_an_unknown_kind_are_refused_before_anything_is_written(s: Setup) -> None:
    with pytest.raises(ValueError):
        s.rotation().rotate(DEPLOYMENT, "control", "short", "T")
    with pytest.raises(ValueError):
        s.rotation().rotate(DEPLOYMENT, "api", NEW, "T")
    assert s.row().control_key_next is None and s.wire.calls == []


def test_an_earlier_rotation_is_ended_before_a_new_one_begins(s: Setup) -> None:
    s.wire.crash_after = 1
    with pytest.raises(Crash):
        s.rotation().rotate(DEPLOYMENT, "control", NEW, "T")
    s.wire.crash_after = None
    third = "3c" * 32
    s.rotation().rotate(DEPLOYMENT, "control", third, "T")
    assert s.row().control_key == third and s.accepted_by_service(third)
    assert not s.accepted_by_service(NEW) and not s.accepted_by_service(CONTROL)


# -- the routes of the core ------------------------------------------------------------------
def test_an_admin_rotates_a_key_by_the_route_and_nobody_else_may(s: Setup) -> None:
    path = f"/deployments/{DEPLOYMENT}/keys/control/rotate"
    assert s.core.call("designer", "POST", path).status_code == 403
    assert s.core.call("operator", "POST", path).status_code == 403
    assert s.core.call(None, "POST", path).status_code == 401
    done = s.core.call("admin", "POST", path)
    assert done.status_code == 200
    assert done.json() == {"deployment_id": DEPLOYMENT, "key": "control", "state": "rotated"}
    assert s.accepted_by_service(s.row().control_key or "") and not s.accepted_by_service(CONTROL)
    assert "control_key" not in str(
        s.core.call("admin", "GET", f"/deployments/{DEPLOYMENT}").json()
    )


def test_the_routes_say_what_is_wrong(s: Setup) -> None:
    assert s.core.call("admin", "POST", "/deployments/none/keys/control/rotate").status_code == 404
    assert (
        s.core.call("admin", "POST", f"/deployments/{DEPLOYMENT}/keys/api/rotate").status_code
        == 422
    )
    s.wire.away = True
    away = s.core.call("admin", "POST", f"/deployments/{DEPLOYMENT}/keys/ingest/rotate")
    assert away.status_code == 503 and away.json()["errors"][0]["code"] == "E-KEY-001"
    s.wire.away = False
    recovered = s.core.call("admin", "POST", f"/deployments/{DEPLOYMENT}/keys/recover")
    assert recovered.status_code == 200 and recovered.json()["outcome"] == "taken back"
    again = s.core.call("admin", "POST", f"/deployments/{DEPLOYMENT}/keys/recover")
    assert again.json()["outcome"] == "nothing"


def test_the_core_that_starts_ends_the_rotation_it_left(s: Setup) -> None:
    from flowstat.api.deployments import recover_keys_on_start

    s.wire.crash_after = 1
    with pytest.raises(Crash):
        s.rotation().rotate(DEPLOYMENT, "control", NEW, "T")
    config = s.core.config
    db = connect(config.db_path)
    done = KeyRotation(db, s.wire).recover_all("T")  # what the start does, with the test's wire
    db.close()
    assert done == {DEPLOYMENT: "completed"}
    recover_keys_on_start(config)  # and nothing is left for the real one to do
    assert s.row().control_key == NEW


def test_the_keys_routes_of_a_service_are_closed_to_the_keys_that_start_runs(s: Setup) -> None:
    from tests.unit.api.test_migrate import API_KEY

    for method, path in (("GET", "/keys"), ("POST", "/keys/control/settle")):
        assert s.service.call(API_KEY, method, path).status_code == 403
        assert s.service.call("nope" * 10, method, path).status_code == 401
