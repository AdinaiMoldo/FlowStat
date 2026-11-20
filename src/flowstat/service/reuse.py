"""A run of a campaign does again only the steps whose inputs changed (US-58).

A step whose action only computes (``ActionSpec.repeatable``: reading a form, checking its rows,
adding the regions) gives the same output for the same arguments and the same file. Its result is
kept under a key made of exactly those, and a later run of a campaign that reaches the same key
takes the result instead of computing it. A corrected form changes the content of one file, so the
steps that read it, and every step that is given what they produce, have another key and are done
again; the others are not.

The scheduler knows nothing of this: it sends a command and gets an outcome, as before, and the
history of the run has the same events. A step with an effect (a write to the target database, a
mail, a call to another system) and a step that waits for a person are never taken from an earlier
run. A run that was not started from a campaign keeps nothing and takes nothing.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

from flowstat.actions.base import ActionSpec
from flowstat.engine.runner import Executor, Ok, Outcome
from flowstat.engine.scheduler import Start
from flowstat.repo.reuse import ReuseRepo

KEY_VERSION = "1"  # raise it when an action that only computes changes what it computes


def key_of(spec: ActionSpec, command: Start, data_dir: Path) -> str | None:
    """The key of a call that only computes, or None when the call must be made.

    None for an action that is not repeatable, for an undo, and when the file the action reads
    is not there or is outside the data directory (the action then fails as it always did).
    """
    if not spec.repeatable or spec.effect or spec.waits or command.compensation:
        return None
    digest = hashlib.sha256()
    digest.update(f"{KEY_VERSION}\0{spec.name}\0".encode())
    try:
        digest.update(json.dumps(command.args, sort_keys=True, ensure_ascii=False).encode())
    except (TypeError, ValueError):
        return None  # arguments that cannot be written down cannot be compared
    if spec.reads is not None:
        base = data_dir.resolve()
        path = (base / str(command.args.get(spec.reads, ""))).resolve()
        if not path.is_relative_to(base):
            return None
        try:
            digest.update(b"\0" + hashlib.sha256(path.read_bytes()).digest())
        except OSError:
            return None
    return digest.hexdigest()


def reusing(
    execute: Executor,
    spec_of: Callable[[str], ActionSpec | None],
    db: sqlite3.Connection,
    run_id: str,
    data_dir: Path,
    clock: Callable[[], str],
) -> Executor:
    """``execute`` with the kept results in front of it. The worker threads of a run share ``db``,
    so every use of it is made one at a time."""
    repo = ReuseRepo(db)
    one_at_a_time = threading.Lock()

    def run(command: Start) -> Outcome:
        spec = spec_of(command.action)
        key = None if spec is None else key_of(spec, command, data_dir)
        if key is None:
            return execute(command)
        with one_at_a_time:
            found = repo.kept(key)
            # only from an earlier run: two steps of one run that happen to have the same
            # arguments are both computed, so "taken" always means "not done again"
            if found is not None and found[1] != run_id:
                repo.note(run_id, command.step, True, found[1])
                return Ok(found[0])
        outcome = execute(command)
        with one_at_a_time:
            if isinstance(outcome, Ok):
                repo.keep(key, command.action, dict(outcome.output), run_id, command.step, clock())
            repo.note(run_id, command.step, False, None)
        return outcome

    return run
