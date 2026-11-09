"""Failure rate and duration of the runs of every workflow (api-spec section 10)."""

from __future__ import annotations

import sqlite3

from fastapi import Depends

from flowstat.api.deps import get_db
from flowstat.api.models import StatisticsOut, VersionStatisticsOut, WorkflowStatisticsOut
from flowstat.api.secured import SecuredRouter
from flowstat.service.statistics import StatisticsService, WorkflowStatistics

router = SecuredRouter(tags=["statistics"])


def out_of(stats: WorkflowStatistics) -> WorkflowStatisticsOut:
    return WorkflowStatisticsOut(
        workflow=stats.workflow,
        runs=stats.runs,
        running=stats.running,
        finished=stats.finished,
        succeeded=stats.succeeded,
        not_reached=stats.not_reached,
        cancelled=stats.cancelled,
        failed=stats.failed,
        failure_rate=stats.failure_rate,
        average_duration=stats.average_duration,
        versions={
            str(number): VersionStatisticsOut(runs=count.runs, failed=count.failed)
            for number, count in sorted(stats.versions.items())
        },
    )


@router.secured(
    "GET", "/statistics", "statistics:read", summary="Failure rate and duration per workflow"
)
def statistics(db: sqlite3.Connection = Depends(get_db)) -> StatisticsOut:
    return StatisticsOut(workflows=[out_of(s) for s in StatisticsService(db).statistics()])
