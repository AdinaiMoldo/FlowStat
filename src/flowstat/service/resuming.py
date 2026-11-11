"""The runs that a process that starts has to take up (docs/api-spec.md section 5, US-36).

A run that is not finished and is not waiting for a person was being driven when the last process
stopped, and nobody drives it now. A store takes up the runs it owns and no others: one that is
leaving or has left (US-33), and the copy of a run that a service drives, are not its to take.
Whether a step that was running is run again or is uncertain is decided by the recovery
(``RunService.recover``), not here.
"""

from __future__ import annotations

from flowstat.repo.runs import StoredRun
from flowstat.service.runs import RunService

# CREATED: stored and never started. RUNNING, COMPENSATING: steps were in flight or ready.
# WAITING (an approval, or a step only a person can resolve) stays as it is until an answer comes.
INTERRUPTED = frozenset({"CREATED", "RUNNING", "COMPENSATING"})


def interrupted(runs: RunService) -> list[StoredRun]:
    """The runs of this store that no process is driving, oldest first."""
    found: list[StoredRun] = []
    for run in runs.store.runs():
        if run.origin is not None or not runs.ownership.get(run.run_id).owned:
            continue
        status, _ = runs.status(run.run_id)
        if status in INTERRUPTED:
            found.append(run)
    return found
