"""The problems of the regional forms (US-56): opened by a new version of a form, worked on by
people (an assignee, comments), closed by the next version that has no such error.

The rules of what changes when are ``model/problems.py``; this service keeps them in the tables,
dates them with the clock of the core and gives each new problem to the person who sent the form.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence

from flowstat.model import problems as life
from flowstat.model.problems import Event, Problem, ProblemError
from flowstat.model.rules import Finding
from flowstat.repo.problems import ProblemNotFoundError, ProblemRepo
from flowstat.service.users import UserService

SYSTEM = "system"

__all__ = ["SYSTEM", "ProblemNotFoundError", "ProblemService"]


class ProblemService:
    def __init__(
        self, db: sqlite3.Connection, new_id: Callable[[], str], clock: Callable[[], str]
    ) -> None:
        self.repo = ProblemRepo(db)
        self.users = UserService(db)
        self.new_id = new_id
        self.clock = clock

    # -- what a new version of a form does ----------------------------------------------------
    def on_version(
        self, region: str, quarter: str, version: int, findings: Sequence[Finding], sent_by: str
    ) -> None:
        """Bring the problems of the form up to date with the findings of its new version."""
        now = self.clock()
        known = self.repo.of_form(region, quarter)
        for change in life.reconcile(known, findings):
            found, old = change.finding, change.problem
            if old is None:
                assert found is not None
                problem = Problem(
                    self.new_id(), region, quarter, life.key_of(found), found.code,
                    found.indicator, found.cell, found.message, found.actual, found.expected,
                    life.OPEN, sent_by or None, version, version, now, now,
                )  # fmt: skip
                self.repo.add(problem)
                self.repo.add_event(
                    Event(problem.id, now, SYSTEM, "OPENED", found.message, version)
                )
                continue
            for status in change.path:
                self.repo.add_event(
                    Event(old.id, now, SYSTEM, status, self._why(status, version, sent_by), version)
                )
            if found is not None:  # the error is still there, possibly in another cell
                self.repo.update(
                    old.id, at=now, status=change.path[-1], cell=found.cell, message=found.message,
                    actual=found.actual, expected=found.expected, seen_in=version,
                )  # fmt: skip
            else:
                self.repo.update(old.id, at=now, status=change.path[-1])

    @staticmethod
    def _why(status: str, version: int, sent_by: str) -> str:
        who = f" sent by {sent_by}" if sent_by else ""
        if status == life.REVALIDATING:
            return f"version {version}{who} is checked again"
        if status == life.RESOLVED:
            return f"the error is not in version {version}"
        if status == life.REOPENED:
            return f"the error is in version {version}"
        return ""

    # -- what people do -----------------------------------------------------------------------
    def assign(self, problem_id: str, assignee: str | None, by: str) -> Problem:
        problem = self.repo.get(problem_id)
        life.check_assignable(problem)
        if assignee is not None:
            names = {u.name for u in self.users.all() if not u.disabled}
            if assignee not in names:
                raise ProblemError("E-PROB-002", f"there is no user {assignee!r} to assign it to")
        now = self.clock()
        self.repo.set_assignee(problem_id, assignee, now)
        self.repo.add_event(Event(problem_id, now, by, "ASSIGNED", assignee or ""))
        return self.repo.get(problem_id)

    def comment(self, problem_id: str, text: str, by: str) -> Problem:
        problem = self.repo.get(problem_id)
        cleaned = life.checked_comment(text)
        self.repo.add_event(Event(problem.id, self.clock(), by, "COMMENTED", cleaned))
        return self.repo.get(problem_id)

    # -- reading ------------------------------------------------------------------------------
    def get(self, problem_id: str) -> tuple[Problem, list[Event]]:
        return self.repo.get(problem_id), self.repo.events(problem_id)

    def find(
        self,
        quarter: str | None = None,
        region: str | None = None,
        status: str | None = None,
        assignee: str | None = None,
        active: bool = False,
    ) -> list[Problem]:
        return self.repo.find(quarter, region, status, assignee, active)
