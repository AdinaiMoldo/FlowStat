"""Show the project end to end on synthetic data: python scripts/demo.py [--kind process|docker]

What it does, in order, and prints as it goes:

1. makes a database with four users (an admin, a designer, the chief statistician, a clerk);
2. publishes the example workflow of the Proposal with its catalogue;
3. starts the API of the core (``python -m flowstat.serve``) and shows that an operator may not
   publish (403) while the chief statistician may approve;
4. runs the workflow in the core: nine regional forms are read, consolidated, and the run waits for
   the approval of the chief statistician, who gives it;
5. deploys the same version as a separate service (a process, or a container with ``--kind docker``,
   which needs Docker with Linux containers), runs it there and shows that the service answers
   with the same results as the core;
6. stops the service and the core.

Only the standard library is used for the HTTP calls; the forms come from the helper of the tests.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]


from tests.unit.actions.helpers import write_all_forms  # noqa: E402

from flowstat.service.cli import main as cli  # noqa: E402

NAME = "quarterly_consolidation"
INPUTS = {"inputs": {"quarter": "2025Q1"}}
ANSWER = {"step": "approve", "decision": "approved"}


class DemoError(RuntimeError):
    pass


def say(text: str = "") -> None:
    print(text, flush=True)


def step(number: int, text: str) -> None:
    say(f"\n[{number}] {text}")


def http(method: str, url: str, token: str | None = None, body: Any = None) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


def decide(url: str, token: str, body: Any, limit: float = 10.0) -> tuple[int, Any]:
    """Answer an approval. A run that has just reached WAITING may still be held by the thread that
    drove it for a moment; the core says 409 then, and the answer is sent again."""
    deadline = time.monotonic() + limit
    while True:
        status, reply = http("POST", url, token, body)
        if status != 409 or time.monotonic() > deadline:
            return status, reply
        time.sleep(0.05)


def expect(status: int, wanted: int, what: str, body: Any = None) -> None:
    if status != wanted:
        raise DemoError(f"{what}: expected {wanted}, got {status} {body}")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def make_user(db: str, name: str, role: str, *titles: str) -> str:
    out = io.StringIO()
    extra = [part for title in titles for part in ("--title", title)]
    with contextlib.redirect_stdout(out):
        code = cli(["user", "add", name, "--role", role, *extra, "--db", db])
    token = next((ln.split(": ")[1] for ln in out.getvalue().splitlines() if "token: " in ln), "")
    if code != 0 or not token:
        raise DemoError(f"could not make the user {name}")
    say(f"    {name:<5} role {role:<9}" + (f" title {', '.join(titles)}" if titles else ""))
    return token


def wait_for(url: str, token: str | None, status: str, limit: float = 90.0) -> dict[str, Any]:
    deadline = time.monotonic() + limit
    report: dict[str, Any] = {}
    while time.monotonic() < deadline:
        _, report = http("GET", url, token)
        if report and report.get("status") == status:
            return report
        time.sleep(0.1)
    raise DemoError(f"{url} did not reach {status}: {report}")


def show(report: dict[str, Any]) -> None:
    counts: dict[str, int] = {}
    for s in report["steps"]:
        counts[s["state"]] = counts.get(s["state"], 0) + 1
    states = ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
    head = f"    status {report['status']}, outcome {report['outcome']}"
    say(f"{head}; {len(report['steps'])} steps: {states}")


def comparable(report: dict[str, Any]) -> dict[str, Any]:
    """What must be equal between the core and a service (api-spec 7): states, codes, outputs."""
    steps = {}
    for s in report["steps"]:
        output = dict(s["output"] or {})
        output.pop("by", None)  # who answered the approval is not the same in the two
        steps[s["step"]] = (
            s["state"],
            s["code"],
            s["attempts"],
            json.dumps(output, sort_keys=True).replace(report["run_id"], "<run>"),
        )
    return {"status": report["status"], "outcome": report["outcome"], "steps": steps}


def start_core(
    work: Path, port: int, db: str, extra: Sequence[str] = ()
) -> subprocess.Popen[bytes]:
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    command = [
        sys.executable, "-m", "flowstat.serve", "--db", db, "--data", str(work / "forms"),
        "--target", str(work / "results.db"), "--port", str(port), *extra,
    ]  # fmt: skip
    with open(work / "core.log", "wb") as log:  # the child has its own copy of the handle
        process = subprocess.Popen(command, env=environment, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(300):
        if process.poll() is not None:
            raise DemoError(f"the core exited: {(work / 'core.log').read_text()[-500:]}")
        if http_ok(f"http://127.0.0.1:{port}/health"):
            return process
        time.sleep(0.1)
    process.terminate()
    raise DemoError("the core did not answer in 30 s")


def http_ok(url: str) -> bool:
    try:
        return http("GET", url)[0] == 200
    except (urllib.error.URLError, OSError):
        return False


def demo(work: Path, kind: str) -> None:
    db = str(work / "flowstat.db")
    step(1, "users")
    admin = make_user(db, "ada", "admin")
    designer = make_user(db, "dina", "designer")
    chief = make_user(db, "olga", "operator", "chief_statistician")
    clerk = make_user(db, "kira", "operator")
    step(2, "publish the example workflow (22 steps, nine regions, an approval)")
    code = cli(["publish", "--db", db, "--catalogue", str(ROOT / "examples" / "catalogue.yaml"),
                str(ROOT / "examples" / "quarterly_consolidation.yaml")])  # fmt: skip
    if code != 0:
        raise DemoError("publish failed")
    write_all_forms(work / "forms", "2025Q1")
    say("    nine synthetic regional forms written for 2025Q1")

    step(3, "start the API of the core")
    port = free_port()
    core = start_core(work, port, db)
    base = f"http://127.0.0.1:{port}"
    try:
        say(f"    listening on {base}")
        status, body = http("GET", f"{base}/workflows")
        expect(status, 401, "no token", body)
        say("    without a token: 401 " + body["errors"][0]["code"])
        status, body = http("POST", f"{base}/workflows/{NAME}/versions/1/publish", clerk)
        expect(status, 403, "an operator publishing", body)
        say("    an operator who publishes: 403 " + body["errors"][0]["message"])
        status, body = http("GET", f"{base}/workflows", designer)
        expect(status, 200, "a designer listing", body)
        say(f"    a designer may read: {body}")

        step(4, "run it in the core")
        status, body = http("POST", f"{base}/workflows/{NAME}/runs", clerk, INPUTS)
        expect(status, 202, "start", body)
        core_id = body["run_id"]
        say(f"    run {core_id} started (202)")
        status, body = http(
            "POST", f"{base}/workflows/{NAME}/runs", clerk, {"inputs": {"quarter": "Q9"}}
        )
        expect(status, 422, "invalid input", body)
        say(
            "    invalid input: 422 " + body["errors"][0]["field"] + " " + body["errors"][0]["code"]
        )
        show(wait_for(f"{base}/runs/{core_id}", clerk, "WAITING"))
        status, body = decide(f"{base}/runs/{core_id}/decisions", clerk, ANSWER)
        expect(status, 403, "a clerk approving", body)
        say("    a clerk who approves: 403 " + body["errors"][0]["message"])
        status, body = decide(f"{base}/runs/{core_id}/decisions", chief, ANSWER)
        expect(status, 202, "the chief statistician approving", body)
        say(f"    approved by {body['by']}")
        core_report = wait_for(f"{base}/runs/{core_id}", clerk, "FINISHED")
        show(core_report)

        step(5, f"deploy the same version as a service ({kind})")
        status, body = http("POST", f"{base}/deployments", admin, {"workflow": NAME, "kind": kind})
        expect(status, 201, "deploy", body)
        endpoint, service = body["endpoint"], body["id"]
        say(f"    deployment {service}: {body['status']} at {endpoint}")
        try:
            say("    keys shown once: one to start runs, one for the title chief_statistician")
            write_all_forms(work / "deployments" / service / "data", "2025Q1")
            status, health = http("GET", f"{endpoint}/health")
            say(f"    /health: {health}")
            status, started = http("POST", f"{endpoint}/runs", body["api_key"], INPUTS)
            expect(status, 202, "start in the service", started)
            run = started["run_id"]
            wait_for(f"{endpoint}/runs/{run}", body["api_key"], "WAITING")
            status, refused = decide(f"{endpoint}/runs/{run}/decisions", body["api_key"], ANSWER)
            expect(status, 403, "the run key approving", refused)
            say("    the key that starts runs may not approve: 403")
            title_key = body["title_keys"]["chief_statistician"]
            status, done = decide(f"{endpoint}/runs/{run}/decisions", title_key, ANSWER)
            expect(status, 202, "the title key approving", done)
            service_report = wait_for(f"{endpoint}/runs/{run}", body["api_key"], "FINISHED")
            show(service_report)
            if comparable(service_report) != comparable(core_report):
                raise DemoError("the service did not give the same results as the core")
            say(
                "    SAME RESULTS as the core: every step has the same state, error code and output"
            )
        finally:
            step(6, "stop the service")
            status, stopped = http("DELETE", f"{base}/deployments/{service}", admin)
            say(f"    {stopped['status'] if status == 200 else stopped}")
    finally:
        core.terminate()
        core.wait(timeout=20)
    say("\nDone: the core and the service were stopped.")


def main() -> int:
    parser = argparse.ArgumentParser(description="FlowStat end-to-end demonstration")
    parser.add_argument("--kind", choices=["process", "docker"], default="process")
    parser.add_argument("--workdir", help="keep the files here (default: a temporary directory)")
    args = parser.parse_args()
    work = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="flowstat-demo-"))
    work.mkdir(parents=True, exist_ok=True)
    try:
        demo(work, args.kind)
    except DemoError as error:
        say(f"\nThe demonstration stopped: {error}")
        return 1
    finally:
        if not args.workdir:
            shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
