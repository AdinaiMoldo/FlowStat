"""What the apps need from outside: the database, the files, the clocks, identifiers, a pool.

The functions that read the real clock, make identifiers and run jobs on threads are passed in by
the executable that starts the server (``flowstat.serve``); the apps never read them
themselves, so a test can give a clock that does not move, ids that count and a pool that runs
the job before returning (deterministic mode).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from flowstat.service.deployments import Launcher, Packager

Job = Callable[[], None]
Submit = Callable[[Job], None]  # runs a job now or later, on any thread


@dataclass(frozen=True, slots=True)
class AppConfig:
    db_path: str
    data_dir: Path  # the regional forms the actions read
    target: str  # the database the actions write results to
    clock: Callable[[], str]  # ISO 8601 text, for the stored times
    time: Callable[[], float]  # seconds, for the events of a run
    sleep: Callable[[float], None]
    new_id: Callable[[], str]  # identifiers of runs and deployments
    submit: Submit  # how a run is driven in the background
    new_token: Callable[[], str]  # a secret: the key of a deployed service
    bundles: Path  # where the bundles of deployments are made
    workers: int = 4  # steps of one run that may run at the same time
    packager: Packager | None = None  # makes a bundle (deploy/package.py)
    launchers: Mapping[str, Launcher] = field(default_factory=dict)  # kind -> how to start it
    ingest_url: str | None = None  # where services ship their runs (the address of this core)
    allowed_hosts: frozenset[str] | None = None  # hosts http.call may reach; None: any (US-39)
    health: Callable[[str], bool] | None = None  # asks a service if it answers; None: not probed
    demo_logins: Mapping[str, str] | None = (
        None  # name -> token, for the page; None: no route (US-45)
    )
