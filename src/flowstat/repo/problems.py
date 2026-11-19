"""A: the problems of the regional forms and their history (US-56)."""

from __future__ import annotations

import sqlite3

from flowstat.model.problems import ACTIVE, Event, Problem

COLUMNS = (
    "id, region, quarter, key, code, indicator, cell, message, actual, expected, status, assignee,"
    " opened_in, seen_in, created_at, updated_at"
)


class ProblemNotFoundError(LookupError):
    pass


def _problem(row: tuple[object, ...]) -> Problem:
    (pid, region, quarter, key, code, indicator, cell, message, actual, expected, status,
     assignee, opened_in, seen_in, created_at, updated_at) = row  # fmt: skip
    return Problem(
        str(pid), str(region), str(quarter), str(key), str(code), str(indicator), str(cell),
        str(message), str(actual), str(expected), str(status),
        None if assignee is None else str(assignee),
        int(str(opened_in)), int(str(seen_in)), str(created_at), str(updated_at),
    )  # fmt: skip


class ProblemRepo:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    def add(self, p: Problem) -> None:
        try:
            self.db.execute(
                f"INSERT INTO problems ({COLUMNS}) VALUES ({', '.join('?' * 16)})",
                (p.id, p.region, p.quarter, p.key, p.code, p.indicator, p.cell, p.message,
                 p.actual, p.expected, p.status, p.assignee, p.opened_in, p.seen_in,
                 p.created_at, p.updated_at),
            )  # fmt: skip
        except sqlite3.IntegrityError:
            raise ValueError(
                f"the form of {p.region} for {p.quarter} already has {p.key}"
            ) from None

    def get(self, problem_id: str) -> Problem:
        row = self.db.execute(
            f"SELECT {COLUMNS} FROM problems WHERE id = ?", (problem_id,)
        ).fetchone()
        if row is None:
            raise ProblemNotFoundError(problem_id)
        return _problem(row)

    def of_form(self, region: str, quarter: str) -> list[Problem]:
        return self.find(region=region, quarter=quarter)

    def find(
        self,
        quarter: str | None = None,
        region: str | None = None,
        status: str | None = None,
        assignee: str | None = None,
        active: bool = False,
    ) -> list[Problem]:
        where, args = [], []
        for column, value in (
            ("quarter", quarter),
            ("region", region),
            ("status", status),
            ("assignee", assignee),
        ):
            if value is not None:
                where.append(f"{column} = ?")
                args.append(value)
        if active:
            where.append(f"status IN ({', '.join('?' * len(ACTIVE))})")
            args.extend(sorted(ACTIVE))
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        rows = self.db.execute(
            f"SELECT {COLUMNS} FROM problems{clause} ORDER BY rowid", args
        ).fetchall()
        return [_problem(r) for r in rows]

    def update(
        self,
        problem_id: str,
        *,
        at: str,
        status: str | None = None,
        cell: str | None = None,
        message: str | None = None,
        actual: str | None = None,
        expected: str | None = None,
        seen_in: int | None = None,
    ) -> None:
        """Change what is given; everything else stays."""
        sets, args = ["updated_at = ?"], [at]
        for column, value in (
            ("status", status),
            ("cell", cell),
            ("message", message),
            ("actual", actual),
            ("expected", expected),
            ("seen_in", seen_in),
        ):
            if value is not None:
                sets.append(f"{column} = ?")
                args.append(value)  # type: ignore[arg-type]
        self.db.execute(f"UPDATE problems SET {', '.join(sets)} WHERE id = ?", (*args, problem_id))

    def set_assignee(self, problem_id: str, assignee: str | None, at: str) -> None:
        self.db.execute(
            "UPDATE problems SET assignee = ?, updated_at = ? WHERE id = ?",
            (assignee, at, problem_id),
        )

    def add_event(self, e: Event) -> None:
        self.db.execute(
            "INSERT INTO problem_events (problem_id, at, by, kind, text, version)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (e.problem_id, e.at, e.by, e.kind, e.text, e.version),
        )

    def events(self, problem_id: str) -> list[Event]:
        rows = self.db.execute(
            "SELECT problem_id, at, by, kind, text, version FROM problem_events"
            " WHERE problem_id = ? ORDER BY seq",
            (problem_id,),
        ).fetchall()
        return [
            Event(
                str(r[0]),
                str(r[1]),
                str(r[2]),
                str(r[3]),
                str(r[4]),
                None if r[5] is None else int(r[5]),
            )
            for r in rows
        ]
