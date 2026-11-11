"""The waiting steps whose time has run out (docs/dsl-spec.md section 5.6, US-38).

Nothing stays in memory while a step waits for a person, so the time limit is found the way an
answer is: from the log. A run whose step waits is rebuilt, the scheduler says when the step runs
out (the recorded time of the wait plus the ``timeout``), and the run is due when that is not in
the future. A version never changes (DR-03), so whether it has a limit at all is learnt once.
"""

from __future__ import annotations

from collections.abc import Callable

from flowstat.service.runs import RunService


class Limits:
    """Which published versions have a step with a ``timeout`` (compiled once for each)."""

    def __init__(self) -> None:
        self._known: dict[tuple[str, int], bool] = {}

    def has(self, runs: RunService, workflow: str, version: int) -> bool:
        key = (workflow, version)
        if key not in self._known:
            plan = runs.workflows.get_plan(workflow, version)
            self._known[key] = any(step.timeout is not None for step in plan.steps)
        return self._known[key]


def due(
    runs: RunService,
    now: float,
    workers: int,
    idempotent: Callable[[str], bool],
    limits: Limits,
) -> list[str]:
    """The runs of this store with a waiting step that has run out of time, oldest first."""
    found: list[str] = []
    for run in runs.store.runs():
        if run.origin is not None or not runs.ownership.get(run.run_id).owned:
            continue
        status, _ = runs.status(run.run_id)
        if status != "WAITING" or not limits.has(runs, run.workflow, run.version):
            continue
        scheduler = runs.recover(run.run_id, workers, now, idempotent).scheduler
        if any(deadline <= now for deadline in scheduler.deadlines().values()):
            found.append(run.run_id)
    return found
