"""Fail the waiting steps that ran out of time (docs/api-spec.md section 5, US-38)."""

from __future__ import annotations

import logging
import sqlite3

from fastapi import FastAPI

from flowstat.actions.registry import Registry
from flowstat.api.config import AppConfig
from flowstat.api.edge import Ticker
from flowstat.api.locks import RunLocks
from flowstat.api.runs import Prepare, drive_in_background
from flowstat.engine.scheduler import Command
from flowstat.repo.db import connect
from flowstat.service.expiring import Limits, due
from flowstat.service.runs import Launch, RunService
from flowstat.service.workflows import WorkflowService

log = logging.getLogger("flowstat.api")


def expire_due(
    config: AppConfig, registry: Registry, locks: RunLocks, limits: Limits | None = None
) -> list[str]:
    """Fail every waiting step of this store whose time ran out, and drive what follows.

    The time is ``config.time()``, so a test moves it and a restart after the limit finds the step
    already late. A run that is being driven or answered at this moment is left for the next
    call. Returns the ids of the runs it took up.
    """
    limits = limits or Limits()
    db: sqlite3.Connection | None = None
    try:
        db = connect(config.db_path)
        workflows = WorkflowService(
            db, known_actions=registry.names(), signatures=registry.signatures()
        )
        ids = due(
            RunService(db, workflows),
            config.time(),
            config.workers,
            registry.is_idempotent,
            limits,
        )
    except Exception:
        log.exception("the steps that ran out of time could not be looked for")
        return []
    finally:
        if db is not None:
            db.close()
    taken: list[str] = []
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
        taken.append(run_id)
    if taken:
        log.info("steps failed for want of an answer, runs taken up: %s", taken)
    return taken


def prepare_for(config: AppConfig, registry: Registry, run_id: str) -> Prepare:
    def prepare(service: RunService) -> tuple[Launch, list[Command] | None]:
        now = config.time()
        launch = service.recover(run_id, config.workers, now, registry.is_idempotent)
        expired = launch.scheduler.expire(now)
        return launch, [*launch.commands, *expired]

    return prepare


def start_expiry(app: FastAPI, config: AppConfig, interval: float) -> Ticker | None:
    """Start the thread that fails the waiting steps that run out of time, every ``interval``
    seconds. None when ``interval`` is not positive (then the check is made at start only)."""
    if interval <= 0:
        return None
    limits = Limits()
    ticker = Ticker(
        lambda: expire_due(config, app.state.registry, app.state.locks, limits), interval, "expiry"
    )
    ticker.start()
    return ticker
