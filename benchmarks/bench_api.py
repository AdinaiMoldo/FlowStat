"""The cost of a request and of a deployment (M6).

Run: python benchmarks/bench_api.py

Everything is measured with the code that serves, with these limits stated in the output:

* a request is measured through the test client of FastAPI, in this process, so the figures are the
  work of the app (authentication, a SQLite connection, the service, the answer) and not of a
  network or of uvicorn;
* a run is driven by the pool of the app; "start" below includes the run itself up to the approval
  (the pool is a function that runs the job before the reply), the real pool is measured apart;
* a deployment is a real process: the time between the request and the first answer of /health,
  and the size of the bundle on disk.

The conclusion is kept in docs/research/api-measurement.md.
"""

from __future__ import annotations

import itertools
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from tests.unit.actions.helpers import write_all_forms
from tests.unit.api.helpers import EXAMPLE, NAME, Env, make_env

from flowstat.deploy.launchers import ProcessLauncher
from flowstat.deploy.package import build_bundle

QUARTER = {"inputs": {"quarter": "2025Q1"}}
REPEAT = 100


def timed(call: Callable[[], object], repeat: int = REPEAT) -> list[float]:
    times = []
    for _ in range(repeat):
        start = time.perf_counter()
        call()
        times.append((time.perf_counter() - start) * 1000)
    return times


def row(name: str, times: list[float]) -> str:
    ordered = sorted(times)
    p95 = ordered[int(0.95 * (len(ordered) - 1))]
    return f"| {name} | {len(times)} | {statistics.median(times):.1f} | {p95:.1f} |"


def prepared(root: Path, **options: object) -> Env:
    root.mkdir(parents=True, exist_ok=True)
    env = make_env(root, **options)  # type: ignore[arg-type]
    env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish")
    write_all_forms(env.config.data_dir, "2025Q1")
    return env


def requests_table(root: Path) -> None:
    env = prepared(root / "a")
    print("| request | n | median ms | p95 ms |")
    print("| --- | --- | --- | --- |")
    health = TestClient(env.client.app)
    print(row("GET /health (no token)", timed(lambda: health.get("/health"))))
    print(row("GET /workflows", timed(lambda: env.call("operator", "GET", "/workflows"))))
    print(
        row(
            "POST /validate (example, 25 steps)",
            timed(lambda: env.call("designer", "POST", "/validate", json={"text": EXAMPLE})),
        )
    )
    print(
        row(
            "POST start a run (to the approval)",
            timed(
                lambda: env.call("operator", "POST", f"/workflows/{NAME}/runs", json=QUARTER), 30
            ),
        )
    )
    print(
        row("GET /runs/{id} (report)", timed(lambda: env.call("operator", "GET", "/runs/r-0001")))
    )


def listing_table(root: Path) -> None:
    env = prepared(root / "b")
    print()
    print("| runs stored | GET /runs median ms |")
    print("| --- | --- |")
    stored = 0
    for target in (1, 10, 25, 50):
        while stored < target:
            env.call("operator", "POST", f"/workflows/{NAME}/runs", json=QUARTER)
            stored += 1
        times = timed(lambda: env.call("operator", "GET", "/runs"), 10)
        print(f"| {stored} | {statistics.median(times):.1f} |")


def pool_table(root: Path) -> None:
    print()
    print("| pool | runs | seconds | runs per second |")
    print("| --- | --- | --- | --- |")
    for size in (1, 4):
        pool = ThreadPoolExecutor(max_workers=size)
        env = prepared(root / f"c{size}", submit=lambda job, p=pool: p.submit(job) and None)  # type: ignore[misc]
        start = time.perf_counter()
        count = 12
        for _ in range(count):
            env.call("operator", "POST", f"/workflows/{NAME}/runs", json=QUARTER)
        pool.shutdown(wait=True)
        seconds = time.perf_counter() - start
        print(f"| {size} thread(s) | {count} | {seconds:.2f} | {count / seconds:.1f} |")


def deployment_table(root: Path) -> None:
    import os
    import subprocess
    from functools import partial

    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    launcher = ProcessLauncher(popen=partial(subprocess.Popen, env=environment), limit=60.0)
    env = prepared(root / "d", packager=build_bundle, launchers={"process": launcher})
    times = []
    print()
    print("| deployment (process) | seconds |")
    print("| --- | --- |")
    for n in range(3):
        start = time.perf_counter()
        made = env.call("admin", "POST", "/deployments", json={"workflow": NAME}).json()
        seconds = time.perf_counter() - start
        times.append(seconds)
        print(f"| request to first /health, run {n + 1} | {seconds:.2f} |")
        env.call("admin", "DELETE", f"/deployments/{made['id']}")
    ids = itertools.count(1)
    sizes = {}
    for kind in ("process", "docker"):
        bundle = build_bundle(
            root / "sizes", f"s{next(ids)}", _version(env), "0" * 64, "2026-11-08T10:00:00Z", kind
        )
        sizes[kind] = sum(f.stat().st_size for f in bundle.rglob("*") if f.is_file())
    print()
    print(
        f"bundle on disk: process {sizes['process'] / 1024:.1f} KiB, "
        f"docker {sizes['docker'] / 1024:.0f} KiB (the copy of the package is most of it)"
    )


def _version(env: Env):  # type: ignore[no-untyped-def]
    from flowstat.repo.db import connect
    from flowstat.service.workflows import WorkflowService

    db = connect(env.config.db_path)
    try:
        return WorkflowService(db).get(NAME, 1)
    finally:
        db.close()


def main() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        requests_table(root)
        listing_table(root)
        pool_table(root)
        deployment_table(root)


if __name__ == "__main__":
    main()
