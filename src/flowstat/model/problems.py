"""Problems: what a region has to correct in its form (US-56).

A problem is opened for each *error* of a form, not for a warning. It is the same problem for the
same code and the same indicator, wherever the cell is (the identity of ``form_versions``): a
negative value that moved to another row is the same mistake. The problem has an assignee, a
comment can be added, and every change is kept as an event, so the history says who did what.

When a new version of the form is accepted, the problems of its region and quarter are brought
up to date (``reconcile``): a problem whose error is gone passes REVALIDATING and is RESOLVED; one
whose error is still there passes REVALIDATING and is REOPENED; a new error opens a problem; a
problem that was resolved and whose error is back is REOPENED. Nobody can mark a problem resolved
by hand, because a person who could would be saying the form is right without a check.

Pure: no storage, no clock. The module uses the standard library only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from flowstat.model.diagnostics import Severity
from flowstat.model.rules import Finding

OPEN = "OPEN"
REVALIDATING = "REVALIDATING"
RESOLVED = "RESOLVED"
REOPENED = "REOPENED"
STATUSES = frozenset({OPEN, REVALIDATING, RESOLVED, REOPENED})
ACTIVE = frozenset({OPEN, REVALIDATING, REOPENED})  # still needs work

COMMENT_MAX = 2000


class ProblemError(Exception):
    """A request about a problem that cannot be done; ``code`` is the E-PROB code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class Problem:
    id: str
    region: str
    quarter: str
    key: str  # ``VAL-RANGE-001|I04``: the code and the indicator (the cell when there is none)
    code: str
    indicator: str
    cell: str  # where the error was last seen
    message: str
    actual: str
    expected: str
    status: str
    assignee: str | None
    opened_in: int  # the version of the form in which the error was first seen
    seen_in: int  # the latest version of the form in which it was seen
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class Event:
    """One line of the history of a problem."""

    problem_id: str
    at: str
    by: str  # a user, or ``system`` for what a new version of the form did
    kind: str  # OPENED, ASSIGNED, COMMENTED, REVALIDATING, RESOLVED, REOPENED
    text: str = ""  # the comment, the assignee, or the reason
    version: int | None = None  # the version of the form that caused it


@dataclass(frozen=True, slots=True)
class Change:
    """What a new version does to one problem: ``problem`` is None when it is a new one, and
    ``path`` is the statuses it passes (the last one is where it stays)."""

    problem: Problem | None
    finding: Finding | None  # the error as it is in the new version; None when it is gone
    path: tuple[str, ...]


def key_of(finding: Finding) -> str:
    return f"{finding.code}|{finding.indicator or finding.cell}"


def reconcile(problems: Sequence[Problem], findings: Sequence[Finding]) -> tuple[Change, ...]:
    """The changes that the findings of a new version make to the problems of its form."""
    errors: dict[str, Finding] = {}
    for found in findings:
        if found.severity is Severity.ERROR:
            errors.setdefault(key_of(found), found)
    changes: list[Change] = []
    known: set[str] = set()
    for p in problems:
        known.add(p.key)
        here = errors.get(p.key)
        if p.status in ACTIVE:
            if here is None:
                changes.append(Change(p, None, (REVALIDATING, RESOLVED)))
            else:
                changes.append(Change(p, here, (REVALIDATING, REOPENED)))
        elif here is not None:  # it was resolved and the error is back
            changes.append(Change(p, here, (REOPENED,)))
    changes.extend(Change(None, f, (OPEN,)) for k, f in errors.items() if k not in known)
    return tuple(changes)


def check_assignable(problem: Problem) -> None:
    if problem.status == RESOLVED:
        raise ProblemError("E-PROB-003", "the problem is resolved: nobody has to correct it")


def checked_comment(text: str) -> str:
    cleaned = text.strip()
    if not cleaned or len(cleaned) > COMMENT_MAX:
        raise ProblemError("E-PROB-001", f"a comment has 1 to {COMMENT_MAX} characters")
    return cleaned
