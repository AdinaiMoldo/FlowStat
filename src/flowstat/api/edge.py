"""The edge shared by the executables of the API: real clock, random identifiers, the run pool.

Nothing below the executables reads the clock, makes an identifier or starts a thread; they get
these from here, through ``AppConfig`` (deterministic mode).
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path

from flowstat.api.config import AppConfig, Job
from flowstat.repo.db import connect
from flowstat.service.outbox import Backoff, Outbox, Reply, Send, SendFailed, ShipLoop, ShipReport

log = logging.getLogger("flowstat.api")


def now_text() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class RunPool:
    """The threads that drive runs: at most ``size`` runs at the same time."""

    def __init__(self, size: int) -> None:
        self._pool = ThreadPoolExecutor(max_workers=size, thread_name_prefix="run")
        self._futures: set[Future[None]] = set()

    def submit(self, job: Job) -> None:
        future = self._pool.submit(job)
        self._futures.add(future)
        future.add_done_callback(self._futures.discard)

    def stop(self, limit: float) -> int:
        """Wait up to ``limit`` seconds for the runs in flight. Returns how many are left."""
        _, left = wait(set(self._futures), timeout=limit)
        self._pool.shutdown(wait=False, cancel_futures=True)
        return len(left)


def real_config(
    db_path: str, data_dir: Path, target: str, workers: int, pool: RunPool, bundles: Path
) -> AppConfig:
    return AppConfig(
        db_path=db_path,
        data_dir=data_dir,
        target=target,
        clock=now_text,
        time=time.time,
        sleep=time.sleep,
        new_id=lambda: uuid.uuid4().hex[:12],
        new_token=lambda: secrets.token_hex(32),
        submit=pool.submit,
        bundles=bundles,
        workers=workers,
    )


def http_send(base_url: str, key: str, deployment_id: str, timeout: float = 10.0) -> Send:
    """A ``send`` for the outbox: POST the batch to the core with the ingest key.

    Any answer of the core is a ``Reply``; no answer (the core is down, the connection broke, the
    time ran out) is ``SendFailed``. The ``expected`` of a refused gap is lifted out of the error.
    """
    url = f"{base_url.rstrip('/')}/deployments/{deployment_id}/events"

    def send(payload: dict[str, object]) -> Reply:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return Reply(response.status, _json(response.read()))
        except urllib.error.HTTPError as error:
            body = _json(error.read())
            errors = body.get("errors")
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                expected = errors[0].get("expected")
                if expected is not None:
                    body = {**body, "expected": expected}
            return Reply(error.code, body)
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise SendFailed(str(error)) from None

    return send


def _json(raw: bytes) -> dict[str, object]:
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


class Ticker:
    """A thread that calls ``work`` every ``interval`` seconds until it is stopped.

    The core and the services use it to look for waiting steps that ran out of time (US-38). The
    call is made in the thread, an exception in it is logged and does not end the thread, and
    ``stop`` waits for a call that is running and returns whether the thread ended in time.
    """

    def __init__(self, work: Callable[[], object], interval: float, name: str = "ticker") -> None:
        self.work, self.interval = work, interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.work()
            except Exception:
                log.exception("the periodic check failed")

    def start(self) -> None:
        self._thread.start()

    def stop(self, limit: float) -> bool:
        self._stop.set()
        self._thread.join(limit)
        return not self._thread.is_alive()


class Shipper:
    """The thread of a deployed service that ships its runs to the core (US-31).

    It opens its own connection to the service's database for every round; the runs write theirs
    in other threads. ``stop`` asks it to end, waits for the last round and returns whether it
    ended in time.
    """

    def __init__(
        self,
        db_path: str,
        send: Send,
        idle: float = 1.0,
        backoff: Backoff | None = None,
    ) -> None:
        self.db_path, self.send = db_path, send
        self._stop = threading.Event()
        self._loop = ShipLoop(self._round, self._stop.wait, backoff, idle)
        self._thread = threading.Thread(target=self._loop.run, name="shipper", daemon=True)

    def _round(self) -> ShipReport:
        db = connect(self.db_path)
        try:
            return Outbox(db, self.send).ship_once()
        finally:
            db.close()

    def start(self) -> None:
        self._thread.start()

    def stop(self, limit: float) -> bool:
        self._stop.set()
        self._thread.join(limit)
        return not self._thread.is_alive()
