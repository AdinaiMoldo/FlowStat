"""The command line: publish a workflow, run it with real actions, read the report of a run.

This is the edge of the system, so it is the place that reads the clock and invents run
identifiers; everything below receives them as arguments (deterministic mode).
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from flowstat.actions.base import ActionContext
from flowstat.actions.registry import default_registry
from flowstat.compiler.catalogue import load_catalogue
from flowstat.dsl.reader import read_yaml
from flowstat.engine.runner import run_threaded
from flowstat.engine.scheduler import Command, Start
from flowstat.model.diagnostics import Diagnostic
from flowstat.model.history import RunReport
from flowstat.model.states import RunOutcome, StepState
from flowstat.model.versions import VersionStatus
from flowstat.repo.db import open_database
from flowstat.repo.runs import RunNotFoundError
from flowstat.repo.versions import VersionNotFoundError
from flowstat.service.runs import InvalidInputsError, Launch, NotPublishedError, RunService
from flowstat.service.workflows import PublishError, WorkflowService

EXIT_OK, EXIT_NOT_DONE, EXIT_USAGE, EXIT_WAITING = 0, 1, 2, 3


def open_services(db_path: str) -> tuple[WorkflowService, RunService]:
    db: sqlite3.Connection = open_database(db_path)
    registry = default_registry()
    workflows = WorkflowService(
        db, known_actions=registry.names(), signatures=registry.signatures()
    )
    return workflows, RunService(db, workflows)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _err(text: str) -> int:
    print(text, file=sys.stderr)
    return EXIT_USAGE


def _diagnostics(items: Sequence[Diagnostic]) -> None:
    for d in items:
        print(f"{d.severity.name.lower()} {d.code} line {d.line}, column {d.column}: {d.message}")


# -- publish ------------------------------------------------------------------------------------
def _publish(args: argparse.Namespace) -> int:
    workflows, _ = open_services(args.db)
    if args.catalogue:
        tree, _diags = read_yaml(Path(args.catalogue).read_text(encoding="utf8"))
        catalogue, problems = load_catalogue(tree) if tree is not None else (None, _diags)
        if catalogue is None or problems:
            _diagnostics(problems)
            return EXIT_NOT_DONE
        for list_name, items in catalogue.lists.items():
            workflows.set_catalogue_list(list_name, [dict(i) for i in items])
    text = Path(args.workflow).read_text(encoding="utf8")
    match = re.search(r"^workflow:\s*([A-Za-z0-9_.-]+)", text, re.MULTILINE)
    if match is None:
        return _err("the file has no 'workflow:' line")
    name = match.group(1)
    saved = workflows.save_draft(name, text, _now())
    if saved.diagnostics:
        _diagnostics(saved.diagnostics)
        print(f"saved {name} v{saved.version.number} as a draft; it has errors")
        return EXIT_NOT_DONE
    try:
        version = workflows.publish(name, saved.version.number, _now())
    except PublishError as error:
        _diagnostics(error.diagnostics)
        return EXIT_NOT_DONE
    _diagnostics(saved.findings)  # warnings and notes do not stop a publication
    print(f"published {name} v{version.number}")
    return EXIT_OK


# -- report -------------------------------------------------------------------------------------
def _format_report(report: RunReport) -> str:
    origin = report.started_at or 0.0

    def at(value: float | None) -> str:
        return "-" if value is None else f"{value - origin:.2f}"

    outcome = report.outcome.value if report.outcome is not None else "-"
    lines = [
        f"run {report.run_id}  {report.workflow} v{report.version}  {report.status}  {outcome}",
        f"{'step':<22}{'state':<17}{'start':>8}{'end':>8}{'tries':>7}  error",
    ]
    for s in report.steps:
        error = f"{s.code} {s.message}" if s.code else ""
        tries = str(s.attempts) if s.attempts else "-"
        row = (
            f"{s.step:<22}{s.state.value:<17}{at(s.started_at):>8}{at(s.finished_at):>8}"
            f"{tries:>7}  {error}"
        )
        lines.append(row.rstrip())
    return "\n".join(lines)


def _exit_code(report: RunReport) -> int:
    if report.status == "WAITING":
        return EXIT_WAITING
    return EXIT_OK if report.outcome is RunOutcome.SUCCEEDED else EXIT_NOT_DONE


def _report(args: argparse.Namespace) -> int:
    _, runs = open_services(args.db)
    try:
        report = runs.report(args.run_id)
    except RunNotFoundError:
        return _err(f"there is no run {args.run_id}")
    print(_format_report(report))
    return _exit_code(report)


# -- run ----------------------------------------------------------------------------------------
def _parse_inputs(pairs: Sequence[str]) -> dict[str, object]:
    values: dict[str, object] = {}
    for pair in pairs:
        key, _, raw = pair.partition("=")
        try:
            values[key] = json.loads(raw)
        except ValueError:
            values[key] = raw
    return values


def _latest_published(workflows: WorkflowService, name: str) -> int | None:
    found = [v.number for v in workflows.versions(name) if v.status is VersionStatus.PUBLISHED]
    return found[-1] if found else None


def _answerer(decision: str | None) -> Callable[[Start], Mapping[str, object] | None]:
    def answer(command: Start) -> Mapping[str, object] | None:
        if decision is None:
            return None
        return {"decision": decision, "by": str(command.args.get("role", ""))}

    return answer


def _drive(
    args: argparse.Namespace,
    runs: RunService,
    run_id: str,
    launch: Launch,
    commands: list[Command] | None,
) -> int:
    """Run the scheduler on the thread pool; its events are stored before its commands run."""
    base = ActionContext(run_id, "", Path(args.data), args.target)

    def journal() -> None:
        launch.log.commit(launch.scheduler.events)

    run_threaded(
        launch.scheduler,
        default_registry().executor(base),
        time.time,
        time.sleep,
        _answerer(args.decision),
        journal=journal,
        commands=commands,
    )
    report = runs.report(run_id)
    print(_format_report(report))
    return _exit_code(report)


def _run(args: argparse.Namespace) -> int:
    workflows, runs = open_services(args.db)
    number = args.version if args.version is not None else _latest_published(workflows, args.name)
    if number is None:
        return _err(f"{args.name} has no published version; publish it first")
    run_id = args.run_id or uuid.uuid4().hex[:12]
    try:
        runs.start(run_id, args.name, number, _parse_inputs(args.input), _now())
    except VersionNotFoundError:
        return _err(f"there is no workflow {args.name} v{number}")
    except NotPublishedError as error:
        return _err(str(error))
    except InvalidInputsError as error:
        return _err("; ".join(f"{e.code} {e.message}" for e in error.errors))
    return _drive(args, runs, run_id, runs.launch(run_id, args.workers), None)


# -- resume and resolve -------------------------------------------------------------------------
def _resume(args: argparse.Namespace) -> int:
    """Take up a run after the engine stopped: finished steps stay, running ones are repeated."""
    _, runs = open_services(args.db)
    try:
        launch = runs.recover(
            args.run_id, args.workers, time.time(), default_registry().is_idempotent
        )
    except RunNotFoundError:
        return _err(f"there is no run {args.run_id}")
    commands = list(launch.commands)
    if args.decision is not None:  # the answer an approval was waiting for has arrived
        for step in launch.scheduler.waiting_steps():
            role = launch.scheduler.arguments(step).get("role", "")
            reply = {"decision": args.decision, "by": str(role)}
            commands += launch.scheduler.signal(step, reply, time.time())
    return _drive(args, runs, args.run_id, launch, commands)


def _resolve(args: argparse.Namespace) -> int:
    """Decide about a step whose outcome is unknown: it is done, or it is to be tried again."""
    output: dict[str, object] = {}
    if args.output is not None:
        try:
            parsed = json.loads(args.output)
        except ValueError:
            return _err("--output must be a JSON object")
        if not isinstance(parsed, dict):
            return _err("--output must be a JSON object")
        output = parsed
    _, runs = open_services(args.db)
    try:
        launch = runs.recover(
            args.run_id, args.workers, time.time(), default_registry().is_idempotent
        )
    except RunNotFoundError:
        return _err(f"there is no run {args.run_id}")
    scheduler = launch.scheduler
    if any(isinstance(c, Start) for c in launch.commands):
        return _err(
            "the run has steps that were running when the engine stopped; "
            f"run 'flowstat resume {args.run_id}' first"
        )
    state = scheduler.states.get(args.step)
    if state is None:
        return _err(f"there is no step {args.step} in run {args.run_id}")
    if state is not StepState.UNCERTAIN:
        return _err(f"{args.step} is not uncertain (it is {state.value})")
    decision = "done" if args.done else "retry"
    commands = scheduler.resolve(args.step, decision, time.time(), output)
    return _drive(args, runs, args.run_id, launch, commands)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flowstat", description="FlowStat workflow engine")
    commands = parser.add_subparsers(dest="command", required=True)

    publish = commands.add_parser("publish", help="save a workflow file and publish its version")
    publish.add_argument("workflow", help="the workflow file (YAML)")
    publish.add_argument("--db", required=True, help="the FlowStat database file")
    publish.add_argument("--catalogue", help="a catalogue file to load before compiling")
    publish.set_defaults(handler=_publish)

    run = commands.add_parser("run", help="run a published workflow")
    run.add_argument("name", help="the workflow name")
    run.add_argument("version", nargs="?", type=int, help="the version (default: latest published)")
    run.add_argument("--db", required=True, help="the FlowStat database file")
    run.add_argument("--data", required=True, help="the directory with the regional forms")
    run.add_argument("--target", required=True, help="the database the results are written to")
    run.add_argument("--input", action="append", default=[], metavar="NAME=VALUE")
    run.add_argument("--workers", type=int, default=4, help="steps that may run at the same time")
    run.add_argument("--decision", choices=["approved", "rejected"], help="answer for approvals")
    run.add_argument("--run-id", help="the run identifier (default: a new random one)")
    run.set_defaults(handler=_run)

    resume = commands.add_parser("resume", help="take up a run after the engine stopped")
    resume.add_argument("run_id")
    resume.add_argument("--db", required=True, help="the FlowStat database file")
    resume.add_argument("--data", required=True, help="the directory with the regional forms")
    resume.add_argument("--target", required=True, help="the database the results are written to")
    resume.add_argument("--workers", type=int, default=4, help="steps that may run at once")
    resume.add_argument(
        "--decision", choices=["approved", "rejected"], help="the answer a waiting approval awaits"
    )
    resume.set_defaults(handler=_resume)

    resolve = commands.add_parser("resolve", help="decide about a step whose outcome is unknown")
    resolve.add_argument("run_id")
    resolve.add_argument("step", help="the uncertain step")
    choice = resolve.add_mutually_exclusive_group(required=True)
    choice.add_argument("--done", action="store_true", help="its effect exists")
    choice.add_argument("--retry", action="store_true", help="run it again (a new attempt)")
    resolve.add_argument(
        "--output", help="with --done: the output the step would have given (JSON)"
    )
    resolve.add_argument("--db", required=True, help="the FlowStat database file")
    resolve.add_argument("--data", required=True, help="the directory with the regional forms")
    resolve.add_argument("--target", required=True, help="the database the results are written to")
    resolve.add_argument("--workers", type=int, default=4, help="steps that may run at once")
    resolve.set_defaults(handler=_resolve, decision=None)

    report = commands.add_parser("report", help="show the history of a run")
    report.add_argument("run_id")
    report.add_argument("--db", required=True, help="the FlowStat database file")
    report.set_defaults(handler=_report)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    code: int = args.handler(args)
    return code


if __name__ == "__main__":
    sys.exit(main())
