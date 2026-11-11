"""The app of the core: workflows, runs, deployments, users (docs/api-spec.md).

``create_app`` builds the app from a configuration; it reads no clock and no environment, so a
test can make as many as it likes.
"""

from __future__ import annotations

from fastapi import FastAPI

from flowstat.actions.registry import default_registry
from flowstat.api import deployments, errors, migrations, resuming, runs, statistics, ui, workflows
from flowstat.api.config import AppConfig
from flowstat.api.locks import RunLocks

DESCRIPTION = """\
REST API of FlowStat. Every route except `/health` needs `Authorization: Bearer <token>`; the role
of the token decides what it may do (`x-permission` on each operation). Every error has the body
`{"errors": [{"code", "message", "field"?, "line"?, "column"?}]}`.
"""


def create_app(config: AppConfig) -> FastAPI:
    app = FastAPI(title="FlowStat", version="0.1.0", description=DESCRIPTION)
    app.state.config = config
    app.state.registry = default_registry()
    app.state.locks = RunLocks()
    errors.install(app)
    app.include_router(workflows.router)
    app.include_router(runs.router)
    app.include_router(runs.shared)
    app.include_router(deployments.router)
    app.include_router(migrations.router)
    app.include_router(statistics.router)
    ui.mount(app)
    migrations.recover_on_start(config, app.state.locks)
    resuming.resume_on_start(config, app.state.registry, app.state.locks)
    return app
