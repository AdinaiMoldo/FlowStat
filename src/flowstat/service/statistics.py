"""Failure rate and duration of the runs of each workflow (docs/api-spec.md section 10, US-34).

Everything is read from the stored events, the copy of the services' runs included, so a figure
may lag behind a service by the events not yet shipped. A *failure* is an outcome of ``FAILED``,
``COMPENSATED`` or ``COMPENSATION_FAILED``; a run that ends ``GOAL_NOT_REACHED`` did not fail, it
reached a result (DR-02), and it is counted apart.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field

from flowstat.model.events import Event
from flowstat.model.history import report_from
from flowstat.model.states import RunOutcome
from flowstat.repo.runs import RunStore, StoredRun

FAILURES = frozenset({RunOutcome.FAILED, RunOutcome.COMPENSATED, RunOutcome.COMPENSATION_FAILED})


@dataclass(slots=True)
class VersionCount:
    runs: int = 0
    failed: int = 0


@dataclass(slots=True)
class WorkflowStatistics:
    workflow: str
    runs: int = 0
    running: int = 0  # not finished: created, running or waiting
    finished: int = 0
    succeeded: int = 0
    not_reached: int = 0
    cancelled: int = 0
    failed: int = 0
    total_duration: float = 0.0
    timed: int = 0  # finished runs whose start and end are both known
    versions: dict[int, VersionCount] = field(default_factory=dict)

    @property
    def failure_rate(self) -> float | None:
        return self.failed / self.finished if self.finished else None

    @property
    def average_duration(self) -> float | None:
        return self.total_duration / self.timed if self.timed else None


def summarise(rows: Iterable[tuple[StoredRun, list[Event]]]) -> list[WorkflowStatistics]:
    """Count the runs of every workflow, in the order of the workflow names."""
    by_name: dict[str, WorkflowStatistics] = {}
    for run, events in rows:
        stats = by_name.setdefault(run.workflow, WorkflowStatistics(run.workflow))
        report = report_from(run.run_id, run.workflow, run.version, events, [])
        version = stats.versions.setdefault(run.version, VersionCount())
        stats.runs += 1
        version.runs += 1
        outcome = report.outcome
        if report.status != "FINISHED" or outcome is None:
            stats.running += 1
            continue
        stats.finished += 1
        if outcome in FAILURES:
            stats.failed += 1
            version.failed += 1
        elif outcome is RunOutcome.SUCCEEDED:
            stats.succeeded += 1
        elif outcome is RunOutcome.GOAL_NOT_REACHED:
            stats.not_reached += 1
        else:
            stats.cancelled += 1
        if report.started_at is not None and report.finished_at is not None:
            stats.total_duration += report.finished_at - report.started_at
            stats.timed += 1
    return [by_name[name] for name in sorted(by_name)]


class StatisticsService:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.store = RunStore(db)

    def statistics(self) -> list[WorkflowStatistics]:
        return summarise((run, self.store.events(run.run_id)) for run in self.store.runs())
