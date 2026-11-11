"""Start the API of the core: ``python -m flowstat.serve --db FILE --data DIR --target FILE``.

This is the edge: it reads the real clock, makes identifiers and owns the pool that drives runs and
the launchers of deployments. When the server stops it waits for the runs in flight for a limit;
the runs that are still going stay in their log as they are, and ``flowstat resume`` takes them up
(docs/api-spec.md section 5). Services that were deployed keep running; they are separate processes.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from flowstat.api.app import create_app
from flowstat.api.edge import RunPool, real_config
from flowstat.api.expiring import start_expiry
from flowstat.deploy.launchers import launchers_for
from flowstat.deploy.package import build_bundle
from flowstat.repo.db import open_database


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m flowstat.serve", description="FlowStat API")
    parser.add_argument("--db", required=True, help="the FlowStat database file")
    parser.add_argument("--data", required=True, help="the directory with the regional forms")
    parser.add_argument("--target", required=True, help="the database the results are written to")
    parser.add_argument("--bundles", help="where deployments are made (default: next to --db)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--ingest-url",
        help="the address deployed services use to reach this core "
        "(default: http://HOST:PORT; with Docker, services reach the host as host.docker.internal)",
    )
    parser.add_argument("--workers", type=int, default=4, help="steps of one run at the same time")
    parser.add_argument("--max-runs", type=int, default=4, help="runs driven at the same time")
    parser.add_argument("--stop-limit", type=float, default=30.0, help="seconds to wait at stop")
    parser.add_argument(
        "--expire-interval",
        type=float,
        default=5.0,
        help="seconds between looks for waiting steps that ran out of time (0: only at start)",
    )
    return parser


def _reachable(host: str) -> str:
    """The host as a client here can use it (0.0.0.0 listens everywhere but nobody calls it)."""
    return "127.0.0.1" if host in ("0.0.0.0", "::", "") else host


def main(
    argv: Sequence[str] | None = None,
    serve: Callable[[FastAPI, str, int], None] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    open_database(args.db).close()  # makes the tables if the file is new
    pool = RunPool(args.max_runs)
    bundles = Path(args.bundles) if args.bundles else Path(args.db).resolve().parent / "deployments"
    config = real_config(args.db, Path(args.data), args.target, args.workers, pool, bundles)
    ingest_url = args.ingest_url or f"http://{_reachable(args.host)}:{args.port}"
    config = replace(
        config, packager=build_bundle, launchers=launchers_for(), ingest_url=ingest_url
    )
    app = create_app(config)
    run = serve or (lambda a, host, port: uvicorn.run(a, host=host, port=port))
    ticker = start_expiry(app, config, args.expire_interval)
    try:
        run(app, args.host, args.port)
    finally:
        if ticker is not None:
            ticker.stop(args.stop_limit)
        left = pool.stop(args.stop_limit)
        if left:
            print(
                f"{left} run(s) were still going; take them up with 'flowstat resume'",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
