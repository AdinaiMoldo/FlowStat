"""The edge shared by the executables of the API: real clock, random identifiers, the run pool.

Nothing below the executables reads the clock, makes an identifier or starts a thread; they get
these from here, through ``AppConfig`` (deterministic mode).
"""

from __future__ import annotations

import secrets
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path

from flowstat.api.config import AppConfig, Job


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
