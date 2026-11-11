"""Look at the services of the registry and say which do not answer (docs/api-spec.md section 7,
US-37)."""

from __future__ import annotations

import logging
import sqlite3

from fastapi import FastAPI

from flowstat.api.config import AppConfig
from flowstat.api.edge import Ticker
from flowstat.repo.db import connect
from flowstat.service.deployments import DeploymentService
from flowstat.service.workflows import WorkflowService

log = logging.getLogger("flowstat.api")


def probe_due(config: AppConfig) -> list[str]:
    """Ask every running service for its health and record a change of answer.

    Returns the ids of the deployments whose answer changed. A core that was given no health check
    probes nothing; a check that raises is not an answer and is logged, so the registry keeps what
    it knew.
    """
    if config.health is None:
        return []
    db: sqlite3.Connection | None = None
    try:
        db = connect(config.db_path)
        service = DeploymentService(
            db, WorkflowService(db), config.packager, config.launchers, config.bundles
        )
        return [row.id for row in service.probe(config.clock(), config.health)]
    except Exception:
        log.exception("the services could not be probed")
        return []
    finally:
        if db is not None:
            db.close()


def start_probing(app: FastAPI, config: AppConfig, interval: float) -> Ticker | None:
    """Start the thread that probes the services every ``interval`` seconds. None when
    ``interval`` is not positive or there is no health check (then the registry is probed never)."""
    if interval <= 0 or config.health is None:
        return None
    ticker = Ticker(lambda: probe_due(config), interval, "probe")
    ticker.start()
    return ticker
