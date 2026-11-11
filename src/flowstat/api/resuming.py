"""Take up the runs that the last process left going (docs/api-spec.md section 5, US-36)."""

from __future__ import annotations

import logging
import sqlite3

from flowstat.actions.registry import Registry
from flowstat.api.config import AppConfig
from flowstat.api.locks import RunLocks
from flowstat.api.runs import Prepare, drive_in_background
from flowstat.engine.scheduler import Command
from flowstat.repo.db import connect
from flowstat.service.resuming import interrupted
from flowstat.service.runs import Launch, RunService
from flowstat.service.workflows import WorkflowService

log = logging.getLogger("flowstat.api")


def resume_on_start(config: AppConfig, registry: Registry, locks: RunLocks) -> list[str]:
    """Drive every run this store owns that was left going. Returns the run ids it started.

    The run is rebuilt from its history (a run that was stored and never started has none, and is
    started), so a finished step stays finished and a step that may have taken effect is not run
    again unless it is idempotent. The pool of the configuration does the driving; the lock of a
    run keeps a command that arrives at the same moment from driving it a second time.
    """
    db: sqlite3.Connection | None = None
    try:
        db = connect(config.db_path)
        workflows = WorkflowService(
            db, known_actions=registry.names(), signatures=registry.signatures()
        )
        ids = [run.run_id for run in interrupted(RunService(db, workflows))]
    except Exception:
        log.exception("the runs that were left going could not be listed")
        return []
    finally:
        if db is not None:
            db.close()
    started: list[str] = []
    for run_id in ids:
        if not locks.try_acquire(run_id):
            continue
        try:
            drive_in_background(
                config, registry, locks, run_id, prepare_for(config, registry, run_id)
            )
        except Exception:
            log.exception("the run %s could not be taken up", run_id)
            continue
        started.append(run_id)
    if started:
        log.info("runs taken up at start: %s", started)
    return started


def prepare_for(config: AppConfig, registry: Registry, run_id: str) -> Prepare:
    def prepare(service: RunService) -> tuple[Launch, list[Command] | None]:
        launch = service.recover(run_id, config.workers, config.time(), registry.is_idempotent)
        return launch, launch.commands

    return prepare
