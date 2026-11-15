"""Set up and check the usability exercise of NFR-06 (docs/research/nfr06-exercise.md).

Usage:
    python scripts/exercise_setup.py start [--dir exercise-data] [--port 8000]
    python scripts/exercise_setup.py check WORKFLOW [--dir exercise-data]

``start`` makes a clean database with three users, the nine regions of the synthetic catalogue and
the synthetic forms of the quarter 2025Q1, starts the core, and prints the address of the editor and
the token of the designer. It runs until Ctrl+C. Nothing is published: the person who takes the
exercise does that.

``check`` is for the observer, after the person has published a workflow with the name WORKFLOW. It
starts two runs with the quarter 2025Q1, answers the first with *approved* and the second with
*rejected* as the chief statistician, and prints for each the outcome and the state of every step,
so that the observer can see whether the workflow does what the task says. It does not judge.

Only synthetic data. The tokens are written to ``tokens.json`` in the directory and are of no use
outside it.
"""

from __future__ import annotations

import argparse
import gc
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import demo  # noqa: E402  (the helpers of the demonstration: users, server, HTTP)
from ruamel.yaml import YAML  # noqa: E402
from tests.unit.actions.helpers import write_all_forms  # noqa: E402

QUARTER = "2025Q1"
CATALOGUE = ROOT / "examples" / "catalogue.yaml"


@dataclass
class Setup:
    base: str
    tokens: dict[str, str]
    process: subprocess.Popen[bytes]


def clear(work: Path) -> None:
    """Remove what an earlier start left in ``work``, so that every person gets the same core.

    Only a folder that this script made (it holds ``tokens.json``) or an empty one is cleared; any
    other folder is not touched, since ``--dir`` is typed by a person.
    """
    if not work.exists():
        return
    if any(work.iterdir()) and not (work / "tokens.json").is_file():
        raise demo.DemoError(f"{work} is not the folder of an earlier exercise; give another --dir")
    # A database connection that an earlier start of this process left to the collector keeps
    # its file locked on Windows.
    gc.collect()
    try:
        shutil.rmtree(work)
    except OSError as error:
        raise demo.DemoError(
            f"{work} could not be cleared ({error}); stop the core that is still running (Ctrl+C)"
        ) from error


def prepare(work: Path, port: int) -> Setup:
    """A clean core in ``work`` listening on ``port``; the caller stops the process."""
    clear(work)
    work.mkdir(parents=True)
    db = str(work / "flowstat.db")
    tokens = {
        "admin": demo.make_user(db, "ada", "admin"),
        "designer": demo.make_user(db, "dina", "designer"),
        "chief": demo.make_user(db, "olga", "operator", "chief_statistician"),
        "operator": demo.make_user(db, "kira", "operator"),
    }
    write_all_forms(work / "forms", QUARTER)
    process = demo.start_core(work, port, db)
    base = f"http://127.0.0.1:{port}"
    regions = YAML(typ="safe").load(CATALOGUE.read_text(encoding="utf8"))["regions"]
    status, body = demo.http(
        "PUT", f"{base}/catalogue/regions", tokens["admin"], {"items": regions}
    )
    if status != 200:
        process.terminate()
        raise demo.DemoError(f"the catalogue was not loaded: {status} {body}")
    (work / "tokens.json").write_text(
        json.dumps({"base": base, "tokens": tokens}, indent=2), encoding="utf8"
    )
    return Setup(base, tokens, process)


def known(base: str, tokens: dict[str, str]) -> str:
    """The workflows of the core, for the observer who typed a name that is not there."""
    status, items = demo.http("GET", f"{base}/workflows", tokens["designer"])
    if status != 200 or not items:
        return "The core has no workflows yet: has the person saved and published one?"
    published = [i["name"] for i in items if i["latest_published"] is not None]
    drafts = [i["name"] for i in items if i["latest_published"] is None]
    return (
        f"Published: {', '.join(published) or 'none'}. "
        f"Saved but not published: {', '.join(drafts) or 'none'}."
    )


def run_once(base: str, tokens: dict[str, str], workflow: str, decision: str) -> dict[str, Any]:
    """Start a run, answer its approval as the chief statistician, return the finished report."""
    status, body = demo.http(
        "POST",
        f"{base}/workflows/{workflow}/runs",
        tokens["operator"],
        {"inputs": {"quarter": QUARTER}},
    )
    if status == 404:
        raise demo.DemoError(f"there is no published workflow {workflow!r}. {known(base, tokens)}")
    if status != 202:
        raise demo.DemoError(f"the run did not start: {status} {body}")
    url = f"{base}/runs/{body['run_id']}"
    demo.wait_for(url, tokens["operator"], "WAITING")
    answer = {"step": "approve", "decision": decision}
    status, reply = demo.decide(f"{url}/decisions", tokens["chief"], answer)
    if status != 202:
        raise demo.DemoError(f"the answer was refused: {status} {reply}")
    report: dict[str, Any] = demo.wait_for(url, tokens["operator"], "FINISHED")
    return report


def describe(decision: str, report: dict[str, Any]) -> str:
    steps = ", ".join(f"{s['step']} {s['state']}" for s in report["steps"])
    return (
        f"answer {decision}: status {report['status']}, outcome {report['outcome']}, "
        f"{len(report['steps'])} steps\n    {steps}"
    )


def start(work: Path, port: int) -> int:
    setup = prepare(work, port)
    try:
        print(f"\nThe core is running. Open {setup.base}/ui/ in a browser.")
        print(f"Token of the designer (paste it into the page): {setup.tokens['designer']}")
        print(f"The tokens are also in {work / 'tokens.json'}.")
        print("The observer checks the result with:")
        print(f"    python scripts/exercise_setup.py check NAME_OF_THE_WORKFLOW --dir {work}")
        print("Stop with Ctrl+C.", flush=True)
        while setup.process.poll() is None:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        setup.process.terminate()
    return 0


def check(work: Path, workflow: str) -> int:
    saved = json.loads((work / "tokens.json").read_text(encoding="utf8"))
    for decision in ("approved", "rejected"):
        report = run_once(saved["base"], saved["tokens"], workflow, decision)
        print(describe(decision, report))
    print("\nWhat to look for: after approved, the step that writes the rows ran and the message")
    print("step was skipped; after rejected, the other way round; both outcomes reach the goal.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    kinds = parser.add_subparsers(dest="command", required=True)
    begin = kinds.add_parser("start", help="make a clean core and run it")
    begin.add_argument("--dir", type=Path, default=Path("exercise-data"))
    begin.add_argument("--port", type=int, default=8000)
    look = kinds.add_parser("check", help="run the published workflow with both answers")
    look.add_argument("workflow")
    look.add_argument("--dir", type=Path, default=Path("exercise-data"))
    args = parser.parse_args(argv)
    try:
        if args.command == "start":
            return start(args.dir, args.port)
        return check(args.dir, args.workflow)
    except demo.DemoError as error:
        print(f"\nStopped: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
