"""The app of a deployed service: one frozen version, the same run routes as the core (api-spec 7).

``python -m flowstat.api.service --bundle DIR --port P`` reads the bundle, refuses it if a file no
longer matches its hash, keeps the version and its one key in the service's own database, and
serves. The run routes are the functions of ``api/runs.py``; nothing here differs from the core in
how a run is checked, driven or reported (that is what US-30 is tested with).
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, Request

from flowstat.actions.registry import default_registry
from flowstat.api import errors, handover, runs
from flowstat.api.config import AppConfig
from flowstat.api.deps import get_runs, get_workflows
from flowstat.api.edge import RunPool, Shipper, http_send, real_config
from flowstat.api.locks import RunLocks
from flowstat.api.models import (
    InputOut,
    ManifestOut,
    RunSummary,
    SchemaOut,
    ServiceHealth,
    ServiceStartIn,
    Started,
    StartIn,
)
from flowstat.api.runs import plain
from flowstat.api.secured import SecuredRouter
from flowstat.model.versions import Version, VersionStatus
from flowstat.repo.db import open_database
from flowstat.repo.users import UserExistsError, UserStore
from flowstat.repo.versions import VersionNotFoundError, VersionStore
from flowstat.service.bundle import Bundle, BundleError, read_bundle
from flowstat.service.deployments import DEPLOYMENT_ID, INGEST_KEY, INGEST_URL
from flowstat.service.runs import RunService
from flowstat.service.workflows import WorkflowService

KEY_OWNER = "service"  # the user of the key that starts runs: an operator with no title
CONTROL = "control"  # the user of the key the core presents to move runs in and out
APPROVER = "approver."  # + a title: the user of the key that answers the approvals of that title

router = SecuredRouter(tags=["service"])
listing = SecuredRouter(tags=["runs"])  # the same tag as in the core


def seed(db_path: str, bundle: Bundle) -> None:
    """Keep the frozen version and the key in the service's database (idempotent).

    A service that is started again finds them and checks that they are the ones of the bundle;
    a database that holds another text under the same number is refused.
    """
    db = open_database(db_path)
    try:
        manifest = bundle.manifest
        versions = VersionStore(db)
        try:
            kept = versions.get(manifest.workflow, manifest.version)
        except VersionNotFoundError:
            versions.import_published(
                Version(
                    manifest.workflow,
                    manifest.version,
                    VersionStatus.PUBLISHED,
                    bundle.text,
                    bundle.catalogue,
                    manifest.created_at,
                    manifest.created_at,
                )
            )
        else:
            if kept.text != bundle.text or kept.catalogue != bundle.catalogue:
                raise BundleError("the service database holds another version under this number")
        keys = {KEY_OWNER: (frozenset[str](), manifest.api_key_sha256)}
        for title, key_hash in manifest.title_keys_sha256.items():
            keys[f"{APPROVER}{title}"] = (frozenset({title}), key_hash)
        for name, (titles, key_hash) in keys.items():
            with contextlib.suppress(UserExistsError):  # restarted: the key is already there
                UserStore(db).add(name, "operator", titles, key_hash, manifest.created_at)
        if manifest.control_key_sha256:  # the core's key to move runs in and out (US-33)
            with contextlib.suppress(UserExistsError):
                UserStore(db).add(
                    CONTROL, "admin", frozenset(), manifest.control_key_sha256, manifest.created_at
                )
    finally:
        db.close()


def bundle_of(request: Request) -> Bundle:
    bundle: Bundle = request.app.state.bundle
    return bundle


@router.secured("GET", "/health", "public", summary="Liveness check")
def health(request: Request) -> ServiceHealth:
    bundle = bundle_of(request)
    return ServiceHealth(
        status="ok", workflow=bundle.manifest.workflow, version=bundle.manifest.version
    )


@router.secured("GET", "/schema", "runs:read", summary="The inputs of the workflow")
def schema(request: Request, workflows: WorkflowService = Depends(get_workflows)) -> SchemaOut:
    manifest = bundle_of(request).manifest
    plan = workflows.get_plan(manifest.workflow, manifest.version)
    return SchemaOut(
        workflow=manifest.workflow,
        version=manifest.version,
        inputs=[
            InputOut(
                name=i.name,
                type=i.type,
                required=i.required,
                default=i.default if i.has_default else None,
                pattern=i.pattern,
                ref=i.ref,
                values=list(i.values) if i.values is not None else None,
                minimum=i.minimum,
                maximum=i.maximum,
            )
            for i in plan.inputs
        ],
        manifest=ManifestOut(
            service_id=manifest.service_id,
            text_sha256=manifest.text_sha256,
            catalogue_sha256=manifest.catalogue_sha256,
            created_at=manifest.created_at,
        ),
    )


@listing.secured("GET", "/runs", "runs:read", summary="List runs, newest first")
def runs_list(
    workflow: str | None = None,
    status: str | None = None,
    service: RunService = Depends(get_runs),
) -> list[RunSummary]:
    return [plain(item) for item in runs.list_runs(service, workflow, status)]


@router.secured(
    "POST", "/runs", "runs:start", status_code=202, summary="Start a run of the frozen version"
)
def start(
    body: ServiceStartIn,
    request: Request,
    workflows: WorkflowService = Depends(get_workflows),
    service: RunService = Depends(get_runs),
) -> Started:
    manifest = bundle_of(request).manifest
    return runs.start_run(
        request,
        workflows,
        service,
        manifest.workflow,
        StartIn(inputs=body.inputs, version=manifest.version),
    )


def create_service_app(config: AppConfig, bundle: Bundle) -> FastAPI:
    """The service of ``bundle``; ``config.db_path`` is the service's own database."""
    seed(config.db_path, bundle)
    app = FastAPI(
        title=f"FlowStat service: {bundle.manifest.workflow} v{bundle.manifest.version}",
        version="0.1.0",
        description="One published version, served with the run routes of the core.",
    )
    app.state.config = config
    app.state.bundle = bundle
    app.state.registry = default_registry()
    app.state.locks = RunLocks()
    errors.install(app)
    app.include_router(router)
    app.include_router(listing)
    app.include_router(runs.shared)
    app.include_router(handover.router)
    return app


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m flowstat.api.service", description="A deployed FlowStat service"
    )
    parser.add_argument("--bundle", required=True, help="the directory made by the packager")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--workers", type=int, default=4, help="steps of one run at the same time")
    parser.add_argument("--max-runs", type=int, default=4, help="runs driven at the same time")
    parser.add_argument("--stop-limit", type=float, default=30.0, help="seconds to wait at stop")
    return parser


def shipper_from(env: Mapping[str, str], db_path: str) -> Shipper | None:
    """The shipper the environment asks for: all three variables, or none (no shipping)."""
    url, key, who = env.get(INGEST_URL), env.get(INGEST_KEY), env.get(DEPLOYMENT_ID)
    if not (url and key and who):
        return None
    return Shipper(db_path, http_send(url, key, who))


def main(
    argv: Sequence[str] | None = None,
    serve: Callable[[FastAPI, str, int], None] | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.bundle)
    try:
        bundle = read_bundle(root)
    except BundleError as error:
        print(f"the bundle is not usable: {error}", file=sys.stderr)
        return 2
    pool = RunPool(args.max_runs)
    config = real_config(
        str(root / "service.db"), root / "data", str(root / "target.db"), args.workers, pool, root
    )
    try:
        app = create_service_app(replace(config), bundle)
    except BundleError as error:
        print(f"the bundle is not usable: {error}", file=sys.stderr)
        return 2
    run = serve or (lambda a, host, port: uvicorn.run(a, host=host, port=port))
    shipper = shipper_from(os.environ if env is None else env, config.db_path)
    if shipper is not None:
        shipper.start()
    try:
        run(app, args.host, args.port)
    finally:
        left = pool.stop(args.stop_limit)
        if shipper is not None and not shipper.stop(args.stop_limit):
            print("the last events were not shipped to the core", file=sys.stderr)
        if left:
            print(
                f"{left} run(s) were still going; take them up with 'flowstat resume'",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
