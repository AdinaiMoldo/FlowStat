"""What the apps need from outside: the database, the files, the clocks, identifiers, a pool.

The functions that read the real clock, make identifiers and run jobs on threads are passed in by
the executable that starts the server (``flowstat.api.__main__``); the apps never read them
themselves, so a test can give a clock that does not move, ids that count and a pool that runs
the job before returning (deterministic mode).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

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
    workers: int = 4  # steps of one run that may run at the same time
