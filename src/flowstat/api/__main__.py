"""Start the API of the core: ``python -m flowstat.api --db FILE --data DIR --target FILE``.

This is the edge: it reads the real clock, makes identifiers and owns the pool that drives runs.
When the server stops it waits for the runs in flight for a limit; the runs that are still going
stay in their log as they are, and ``flowstat resume`` takes them up (docs/api-spec.md section 5).
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from flowstat.api.app import create_app
from flowstat.api.config import AppConfig, Job
from flowstat.repo.db import open_database


def _now() -> str:
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


def make_config(args: argparse.Namespace, pool: RunPool) -> AppConfig:
    return AppConfig(
        db_path=args.db,
        data_dir=Path(args.data),
        target=args.target,
        clock=_now,
        time=time.time,
        sleep=time.sleep,
        new_id=lambda: uuid.uuid4().hex[:12],
        submit=pool.submit,
        workers=args.workers,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m flowstat.api", description="FlowStat API")
    parser.add_argument("--db", required=True, help="the FlowStat database file")
    parser.add_argument("--data", required=True, help="the directory with the regional forms")
    parser.add_argument("--target", required=True, help="the database the results are written to")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--workers", type=int, default=4, help="steps of one run at the same time")
    parser.add_argument("--max-runs", type=int, default=4, help="runs driven at the same time")
    parser.add_argument("--stop-limit", type=float, default=30.0, help="seconds to wait at stop")
    return parser


def main(
    argv: Sequence[str] | None = None,
    serve: Callable[[FastAPI, str, int], None] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    open_database(args.db).close()  # makes the tables if the file is new
    pool = RunPool(args.max_runs)
    app = create_app(make_config(args, pool))
    run = serve or (lambda a, host, port: uvicorn.run(a, host=host, port=port))
    try:
        run(app, args.host, args.port)
    finally:
        left = pool.stop(args.stop_limit)
        if left:
            print(
                f"{left} run(s) were still going; take them up with 'flowstat resume'",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
