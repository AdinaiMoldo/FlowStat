"""The cost of what M7 added: a batch of events at the core, shipping, the graph and its drawing.

Run: python benchmarks/bench_m7.py

* A batch is measured through the test client of FastAPI in this process, like bench_api.py, so
  the figure is the work of the app (the ingest key, a SQLite connection, the transaction with
  `PRAGMA synchronous = FULL`) and not of a network.
* Shipping drives `Outbox.ship_once` with that same client as the `send` function.
* The graph function is measured alone; the drawing is measured in Chromium (Playwright) from the
  click on "Show graph" to the last node, and is skipped with a note when Chromium or the fetched
  libraries are missing.

The conclusion is kept in docs/research/m7-measurement.md.
"""

from __future__ import annotations

import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.unit.api.helpers import EXAMPLE, NAME, T, make_env

from flowstat.deploy.package import build_bundle
from flowstat.model.events import Event, EventKind
from flowstat.repo.db import open_database
from flowstat.repo.runs import RunStore
from flowstat.service.outbox import Outbox, Reply
from flowstat.service.workflows import WorkflowService

REPEAT = 30


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
    return f"| {name} | {len(times)} | {statistics.median(times):.2f} | {p95:.2f} |"


class Quiet:
    def start(self, bundle: Path, env: object = None) -> object:
        from flowstat.service.deployments import Running

        return Running("http://127.0.0.1:9100", "pid-1")

    def stop(self, handle: str) -> None:
        pass


def events(n: int, start: int = 0) -> list[dict[str, object]]:
    return [
        {"seq": i, "kind": "STEP_STARTED", "at": float(i), "step": f"s{i % 20}"}
        for i in range(start, start + n)
    ]


def ingest_rows(root: Path) -> list[str]:
    env = make_env(root, packager=build_bundle, launchers={"process": Quiet()})  # type: ignore[dict-item]
    env.call("designer", "POST", f"/workflows/{NAME}/versions", json={"text": EXAMPLE})
    env.call("designer", "POST", f"/workflows/{NAME}/versions/1/publish")
    made = env.call("admin", "POST", "/deployments", json={"workflow": NAME}).json()
    auth = {"Authorization": f"Bearer {made['ingest_key']}"}
    url = f"/deployments/{made['id']}/events"

    def body(run: str, n: int, start: int = 0) -> dict[str, object]:
        return {
            "run": {
                "run_id": run,
                "workflow": NAME,
                "version": 1,
                "inputs": {"quarter": "2025Q1"},
                "created_at": T,
            },
            "events": events(n, start),
        }

    rows = []
    counter = iter(range(10_000))
    for size in (1, 50, 200):
        rows.append(
            row(
                f"new run, {size} event(s)",
                timed(
                    lambda size=size: env.client.post(
                        url, json=body(f"n{next(counter)}", size), headers=auth
                    )
                ),
            )
        )
    env.client.post(url, json=body("dup", 200), headers=auth)
    rows.append(
        row(
            "the same 200 events again",
            timed(lambda: env.client.post(url, json=body("dup", 200), headers=auth)),
        )
    )

    # a service with 1000 events in 5 runs, shipped by the outbox over the client
    service = open_database(":memory:")
    store = RunStore(service)
    for r in range(5):
        store.create(f"s{r}", NAME, 1, {"quarter": "2025Q1"}, T)
        store.append(
            f"s{r}", [Event(i, EventKind.STEP_STARTED, float(i), step="a") for i in range(200)]
        )

    def send(payload: dict[str, object]) -> Reply:
        reply = env.client.post(url, json=payload, headers=auth)
        return Reply(reply.status_code, reply.json())

    start = time.perf_counter()
    report = Outbox(service, send).ship_once()
    seconds = time.perf_counter() - start
    assert report.clean and report.events == 1000
    rows.append(f"| ship 1000 events in 5 runs (one round) | 1 | {seconds * 1000:.0f} | - |")
    return rows


def graph_rows() -> list[str]:
    from flowstat.service.graph import build_graph

    db = open_database(":memory:")
    out = []
    for count in (9, 100):
        service = WorkflowService(db)
        service.set_catalogue_list(
            "regions", [{"code": f"R{i:03d}", "name": f"R{i}"} for i in range(count)]
        )
        text = EXAMPLE.replace("at_least: 7", f"at_least: {max(1, count - 2)}")
        plan = service._compile(text, service.catalogue()).plan
        assert plan is not None
        graph = build_graph(plan)
        out.append(
            row(
                f"graph of {count} regions ({len(graph.nodes)} nodes)",
                timed(lambda p=plan: build_graph(p)),
            )
        )
    return out


def drawing_rows(root: Path) -> list[str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ["| drawing | skipped: Playwright is not installed | | |"]
    import threading

    import uvicorn
    from tests.browser.conftest import VENDOR, free_port

    if not VENDOR.is_file():
        return ["| drawing | skipped: python scripts/fetch_ui.py was not run | | |"]
    env = make_env(root)
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(env.client.app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    out = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for count in (9, 100):
                regions = [{"code": f"R{i:03d}", "name": f"Region {i}"} for i in range(count)]
                env.call("admin", "PUT", "/catalogue/many", {"items": regions})
                text = EXAMPLE.replace("catalogue.regions", "catalogue.many").replace(
                    "at_least: 7", f"at_least: {max(1, count - 2)}"
                )
                times = []
                for _ in range(5):
                    page = browser.new_page()
                    page.goto(f"http://127.0.0.1:{port}/ui/?plain")
                    page.wait_for_selector("body[data-ready]")
                    page.fill("#token", "d2" * 32)
                    page.click("#sign-in button[type=submit]")
                    page.fill("#plain", text)
                    begin = time.perf_counter()
                    page.click("#preview")
                    page.wait_for_function(
                        "document.getElementById('graph').cy !== undefined"
                        " && document.getElementById('graph').cy.nodes().length > 0"
                    )
                    times.append((time.perf_counter() - begin) * 1000)
                    page.close()
                out.append(row(f"drawing, {count} regions (click to last node)", times))
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    return out


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a").mkdir()
        (root / "b").mkdir()
        print("| What | Runs | Median (ms) | p95 (ms) |")
        print("| --- | --- | --- | --- |")
        for line in [*ingest_rows(root / "a"), *graph_rows(), *drawing_rows(root / "b")]:
            print(line)


if __name__ == "__main__":
    main()
