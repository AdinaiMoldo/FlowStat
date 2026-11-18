"""Set up and check the usability exercise of NFR-06 (docs/research/nfr06-exercise.md).

Usage:
    python scripts/exercise_setup.py start [--dir exercise-data] [--port 8000] [--logins]
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
import secrets
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import demo  # noqa: E402  (the helpers of the demonstration: users, server, HTTP)
from ruamel.yaml import YAML  # noqa: E402
from tests.unit.actions.helpers import write_all_forms  # noqa: E402

from flowstat.repo.db import open_database  # noqa: E402
from flowstat.service.users import UserService  # noqa: E402

QUARTER = "2025Q1"
CATALOGUE = ROOT / "examples" / "catalogue.yaml"


# the key in tokens.json, the user, and what the user is
USERS = {
    "admin": ("ada", "admin", ()),
    "designer": ("dina", "designer", ()),
    "chief": ("olga", "operator", ("chief_statistician",)),
    "operator": ("kira", "operator", ()),
}


@dataclass
class Setup:
    base: str
    tokens: dict[str, str]
    process: subprocess.Popen[bytes]
    passwords: dict[str, str] = field(default_factory=dict)  # key -> password, for the page


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


def prepare(work: Path, port: int, logins: bool = False) -> Setup:
    """A clean core in ``work`` listening on ``port``; the caller stops the process.

    With ``logins`` the page shows a button for each user (the core is started with
    ``--demo-logins``); without it the person pastes a token, as the exercise asks.
    """
    if demo.http_ok(f"http://127.0.0.1:{port}/health"):
        # A core of an earlier start would answer in the place of the new one, with its old users.
        raise demo.DemoError(
            f"something already answers on port {port}; stop the core of the earlier start "
            "(Ctrl+C in its window) or give another --port"
        )
    clear(work)
    work.mkdir(parents=True)
    db = str(work / "flowstat.db")
    tokens = {
        key: demo.make_user(db, name, role, *titles) for key, (name, role, titles) in USERS.items()
    }
    # A password for each, to sign in on the page. They are made here, for this start only, and
    # written to tokens.json with the tokens; they are of no use outside this folder.
    passwords = {key: f"demo-{secrets.token_hex(6)}" for key in USERS}
    users = UserService(open_database(db))
    for key, (name, _, _) in USERS.items():
        users.set_password(name, passwords[key], secrets.token_bytes(16))
    write_all_forms(work / "forms", QUARTER)
    base = f"http://127.0.0.1:{port}"
    (work / "tokens.json").write_text(  # before the core starts: --demo-logins reads it
        json.dumps({"base": base, "tokens": tokens, "passwords": passwords}, indent=2),
        encoding="utf8",
    )
    extra = ["--demo-logins", str(work / "tokens.json")] if logins else []
    process = demo.start_core(work, port, db, extra)
    regions = YAML(typ="safe").load(CATALOGUE.read_text(encoding="utf8"))["regions"]
    status, body = demo.http(
        "PUT", f"{base}/catalogue/regions", tokens["admin"], {"items": regions}
    )
    if status != 200:
        process.terminate()
        raise demo.DemoError(f"the catalogue was not loaded: {status} {body}")
    return Setup(base, tokens, process, passwords)


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


def start(work: Path, port: int, logins: bool = False) -> int:
    setup = prepare(work, port, logins)
    try:
        print(f"\nThe core is running. Open {setup.base}/ui/ in a browser.")
        if not logins:
            print(f"Token of the designer (paste it into the page): {setup.tokens['designer']}")
        print("The users of this start, each with a password for POST /login:")
        for key, (name, role, titles) in USERS.items():
            what = f"{role}, {', '.join(titles)}" if titles else role
            print(f"    {name:<5} {setup.passwords[key]}   ({what})")
        if logins:
            print("The page also has a button for each user (a demonstration).")
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
    begin.add_argument(
        "--logins",
        action="store_true",
        help="a demonstration: the page shows a button for each user instead of asking for a "
        "token (the core listens on 127.0.0.1 only)",
    )
    look = kinds.add_parser("check", help="run the published workflow with both answers")
    look.add_argument("workflow")
    look.add_argument("--dir", type=Path, default=Path("exercise-data"))
    args = parser.parse_args(argv)
    try:
        if args.command == "start":
            return start(args.dir, args.port, args.logins)
        return check(args.dir, args.workflow)
    except demo.DemoError as error:
        print(f"\nStopped: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
