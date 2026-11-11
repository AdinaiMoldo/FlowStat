"""The health of the services in the registry (US-37, docs/api-spec.md section 7).

The registry records `running` when a service answered at start and nothing else, so a service
whose process was killed stayed `running` for ever. The probe asks each running service for its
`/health`; a service that does not answer is shown as `unreachable`, and as `running` again once
it answers. A service that was stopped on purpose, or never started, is not asked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flowstat.service.deployments import DeploymentService, NotRunningError
from tests.unit.service.test_deployments import FakeLauncher, deploy, make

LATER = "2026-11-08T10:05:00Z"
LATEST = "2026-11-08T10:10:00Z"


class Answers:
    """A `/health` that can be turned on and off, and that remembers who was asked."""

    def __init__(self, up: bool = True) -> None:
        self.up = up
        self.asked: list[str] = []
        self.script: list[bool] = []  # answers to give first, one for each call

    def __call__(self, endpoint: str) -> bool:
        self.asked.append(endpoint)
        return self.script.pop(0) if self.script else self.up


@pytest.fixture
def started(tmp_path: Path) -> tuple[DeploymentService, FakeLauncher]:
    service, launcher, _ = make(tmp_path)
    deploy(service)
    return service, launcher


def test_a_service_that_answered_at_start_is_running_and_answering(
    started: tuple[DeploymentService, FakeLauncher],
) -> None:
    service, _ = started
    row = service.get("d-1")
    assert row.state == "running" and row.answering is True


def test_a_service_that_does_not_answer_is_shown_as_unreachable_with_the_time(
    started: tuple[DeploymentService, FakeLauncher],
) -> None:
    service, _ = started
    changed = service.probe(LATER, Answers(up=False))
    [row] = changed
    assert row.state == "unreachable" and row.answering is False
    assert row.answering_since == LATER and "does not answer" in (row.detail or "")
    assert row.status == "running"  # the stored word is unchanged: the process was not stopped
    assert service.get("d-1") == row


def test_it_is_running_again_as_soon_as_it_answers_and_the_reason_is_cleared(
    started: tuple[DeploymentService, FakeLauncher],
) -> None:
    service, _ = started
    service.probe(LATER, Answers(up=False))
    [row] = service.probe(LATEST, Answers(up=True))
    assert row.state == "running" and row.answering is True and row.answering_since == LATEST
    assert not row.detail


def test_a_probe_that_finds_nothing_new_changes_nothing_and_writes_nothing(
    started: tuple[DeploymentService, FakeLauncher],
) -> None:
    service, _ = started
    before = service.get("d-1")
    assert service.probe(LATER, Answers(up=True)) == []
    assert service.get("d-1") == before  # not even the time of the row moves
    service.probe(LATER, Answers(up=False))
    unreachable = service.get("d-1")
    assert service.probe(LATEST, Answers(up=False)) == []
    assert service.get("d-1") == unreachable  # the time says since when, not when it was last asked


def test_one_missed_answer_is_not_enough_the_service_is_asked_twice(
    started: tuple[DeploymentService, FakeLauncher],
) -> None:
    service, _ = started
    answers = Answers()
    answers.script = [False, True]  # one lost request, then an answer
    assert service.probe(LATER, answers) == []
    assert service.get("d-1").state == "running" and len(answers.asked) == 2
    answers = Answers(up=False)
    service.probe(LATER, answers)
    assert len(answers.asked) == 2 and service.get("d-1").state == "unreachable"


def test_a_service_stopped_on_purpose_is_not_asked(
    started: tuple[DeploymentService, FakeLauncher],
) -> None:
    service, _ = started
    service.stop("d-1", LATER)
    answers = Answers(up=False)
    assert service.probe(LATEST, answers) == []
    assert answers.asked == [] and service.get("d-1").state == "stopped"


def test_a_service_that_failed_to_start_is_not_asked(tmp_path: Path) -> None:
    service, _, _ = make(tmp_path, FakeLauncher(fail="port 9100 is taken"))
    with pytest.raises(Exception):  # noqa: B017 the row stays as failed
        deploy(service)
    answers = Answers(up=False)
    assert service.probe(LATER, answers) == [] and answers.asked == []
    assert service.get("d-1").state == "failed"


def test_an_unreachable_service_can_be_stopped_and_is_then_stopped_not_unreachable(
    started: tuple[DeploymentService, FakeLauncher],
) -> None:
    service, launcher = started
    service.probe(LATER, Answers(up=False))
    row = service.stop("d-1", LATEST)
    assert row.state == "stopped" and row.answering is None
    assert launcher.stopped == ["pid-77"]
    with pytest.raises(NotRunningError):
        service.stop("d-1", LATEST)


def test_each_service_is_judged_by_its_own_endpoint(tmp_path: Path) -> None:
    service, _, _ = make(tmp_path)
    tokens = iter(f"{n:064x}" for n in range(1, 20))
    ids = iter(["d-1", "d-2"])
    for _ in range(2):
        service.deploy(
            "quarterly_consolidation", 1, "process", lambda: next(ids), lambda: next(tokens), "T"
        )
    service.store.update("d-2", "T", status="running", endpoint="http://127.0.0.1:9200")

    def only_first(endpoint: str) -> bool:
        return endpoint.endswith(":9100")

    [row] = service.probe(LATER, only_first)
    assert row.id == "d-2" and row.state == "unreachable"
    assert service.get("d-1").state == "running"


def test_a_row_that_was_made_before_the_probe_existed_is_unknown_not_unreachable(
    started: tuple[DeploymentService, FakeLauncher],
) -> None:
    service, _ = started
    service.store.db.execute("UPDATE deployments SET answering = NULL, answering_since = NULL")
    assert service.get("d-1").state == "running" and service.get("d-1").answering is None
    [row] = service.probe(LATER, Answers(up=True))
    assert row.answering is True


def test_a_real_server_that_goes_away_is_seen_to_go_away_with_the_real_health_check(
    tmp_path: Path,
) -> None:
    """The check the core uses (``http_healthy``) against a real socket, not a function that is
    passed in: the same registry row is running while the server answers and unreachable after."""
    import http.server
    import threading

    from flowstat.deploy.launchers import http_healthy

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200 if self.path == "/health" else 404)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = f"http://127.0.0.1:{server.server_address[1]}"
    service, _, _ = make(tmp_path)
    deploy(service)
    service.store.update("d-1", LATER, status="running", endpoint=endpoint)
    try:
        assert service.probe(LATER, http_healthy) == []  # answers: nothing changes
        assert service.get("d-1").state == "running"
    finally:
        server.shutdown()
        server.server_close()
    [row] = service.probe(LATEST, http_healthy)
    assert row.state == "unreachable"
