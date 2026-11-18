"""A synthetic history for the demonstration, so that no page of it is empty (US-52).

``seed`` is called by ``exercise_setup.py start --with-history`` before the core starts. It makes
the history in this process, through the same API the page uses, with a clock that is given to it:
the example workflow published, and nine runs of it over the last two weeks (six reached the goal,
one stopped at a form with a mistake, one did not reach the goal, one waits for the chief
statistician), then a second version, so that the run that waits can be moved to it, and two
campaigns (the quarter of the last run is being validated, the quarter of the live demonstration is
collecting). The runs
belong to the quarters 2022Q4 to 2024Q4, so the quarter 2025Q1 of the live demonstration stays
empty. Only synthetic data.
"""

from __future__ import annotations

import gc
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import make_forms
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from flowstat.api.app import create_app
from flowstat.api.config import AppConfig

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "quarterly_consolidation.yaml"
CATALOGUE = ROOT / "examples" / "catalogue.yaml"
NAME = "quarterly_consolidation"
STARTERS = {"operator": "atai", "operator2": "marina"}  # the key of the token, the user


@dataclass(frozen=True, slots=True)
class Item:
    days_ago: int
    at: str  # the time of the day, HH:MM, UTC
    quarter: str
    kind: str  # ok | not_reached | failed | waiting
    starter: str
    minutes_to_answer: int = 0  # how long the chief statistician took, for the runs that got to her


PLAN = (
    Item(13, "09:40", "2023Q3", "ok", "operator", 12),
    Item(11, "10:15", "2023Q4", "ok", "operator2", 25),
    Item(9, "14:05", "2024Q1", "ok", "operator", 8),
    Item(8, "11:30", "2024Q2", "not_reached", "operator2"),
    Item(6, "09:55", "2024Q3", "failed", "operator"),
    Item(5, "16:20", "2022Q4", "ok", "operator2", 40),
    Item(3, "10:10", "2023Q1", "ok", "operator", 15),
    Item(2, "13:45", "2023Q2", "ok", "operator2", 9),
    Item(0, "", "2024Q4", "waiting", "operator"),  # started 40 minutes before ``now``
)


# name, year, quarter, deadline in days from now, the changes made after the draft
CAMPAIGNS = (
    (
        "Quarterly Regional Statistics, Q4 2024", 2024, 4, -6,
        ("OPEN", "COLLECTING", "VALIDATING"),
    ),
    (
        "Quarterly Regional Statistics, Q1 2025", 2025, 1, 12,
        ("OPEN", "COLLECTING"),
    ),
)  # fmt: skip


class Timeline:
    """The clock of the history: a time that is set for each step of the story and moves a little
    at every reading of the seconds, so that a run has a duration."""

    def __init__(self, now: datetime) -> None:
        self.at = now
        self.elapsed = 0.0

    def set(self, moment: datetime) -> None:
        self.at, self.elapsed = moment, 0.0

    def clock(self) -> str:
        return (self.at + timedelta(seconds=self.elapsed)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def time(self) -> float:
        self.elapsed += 0.35
        return self.at.timestamp() + self.elapsed


def open_client(work: Path, db: str, now: datetime, timeline: Timeline | None = None) -> TestClient:
    """The API of the core in this process, on the files of ``work``, with the clock of the history.

    Runs are driven before the reply (there is no pool), identifiers come from a generator with a
    seed, so the same call gives the same history.
    """
    line = timeline or Timeline(now)
    rng = random.Random(52)
    config = AppConfig(
        db_path=db,
        data_dir=work / "forms",
        target=str(work / "results.db"),
        clock=line.clock,
        time=line.time,
        sleep=lambda seconds: None,
        new_id=lambda: rng.randbytes(6).hex(),
        new_token=lambda: rng.randbytes(32).hex(),
        submit=lambda job: job(),
        bundles=work / "bundles",
        workers=2,
    )
    client = TestClient(create_app(config))
    client.timeline = line  # type: ignore[attr-defined]
    return client


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def write_quarter(data: Path, quarter: str, kind: str) -> None:
    """The forms of one quarter: all good, six of nine, or all with one mistake in BAT."""
    for region in make_forms.REGIONS:
        if kind == "not_reached" and region in make_forms.MISSING:
            continue
        folder = data / region
        folder.mkdir(parents=True, exist_ok=True)
        rows = make_forms.rows_of(
            region, mistake=kind == "failed" and region == make_forms.MISTAKE_IN
        )
        make_forms.write_form(folder / f"{quarter}.xlsx", rows)


def moment(now: datetime, item: Item) -> datetime:
    if item.kind == "waiting":
        return now - timedelta(minutes=40)
    hour, minute = (int(part) for part in item.at.split(":"))
    day = (now - timedelta(days=item.days_ago)).replace(hour=hour, minute=minute, second=0)
    return min(day, now - timedelta(hours=1))


def expect(reply: Any, status: int, what: str) -> None:
    if reply.status_code != status:
        raise RuntimeError(f"the history could not {what}: {reply.status_code} {reply.text[:300]}")


def seed(work: Path, db: str, tokens: dict[str, str], now: datetime) -> dict[str, Any]:
    """Make the history in ``work``; the users of ``tokens`` must exist in ``db`` already.

    ``now`` is when the demonstration starts: no run is later than it.
    """
    now = now.astimezone(UTC)
    client = open_client(work, db, now)
    line: Timeline = client.timeline  # type: ignore[attr-defined]
    try:
        line.set(now - timedelta(days=14))
        regions = YAML(typ="safe").load(CATALOGUE.read_text(encoding="utf8"))["regions"]
        expect(
            client.put(
                "/catalogue/regions", headers=bearer(tokens["admin"]), json={"items": regions}
            ),
            200,
            "load the catalogue",
        )
        designer = bearer(tokens["designer"])
        expect(
            client.post(
                f"/workflows/{NAME}/versions",
                headers=designer,
                json={"text": EXAMPLE.read_text(encoding="utf8")},
            ),
            201,
            "save the workflow",
        )
        expect(
            client.post(f"/workflows/{NAME}/versions/1/publish", headers=designer), 200, "publish"
        )
        started_by: list[str] = []
        for item in sorted(PLAN, key=lambda i: moment(now, i)):
            write_quarter(work / "forms", item.quarter, item.kind)
            begin = moment(now, item)
            line.set(begin)
            reply = client.post(
                f"/workflows/{NAME}/runs",
                headers=bearer(tokens[item.starter]),
                json={"inputs": {"quarter": item.quarter}},
            )
            expect(reply, 202, f"start the run of {item.quarter}")
            started_by.append(STARTERS[item.starter])
            if item.kind == "ok":  # the others stop before the approval, or wait for it
                line.set(begin + timedelta(minutes=item.minutes_to_answer))
                expect(
                    client.post(
                        f"/runs/{reply.json()['run_id']}/decisions",
                        headers=bearer(tokens["chief"]),
                        json={"step": "approve", "decision": "approved"},
                    ),
                    202,
                    f"answer the run of {item.quarter}",
                )
        # a second version, published after the runs: the one that waits is on the first, so the
        # page of migration has a run that can be moved
        line.set(now - timedelta(minutes=20))
        second = EXAMPLE.read_text(encoding="utf8").replace("version: 1", "version: 2", 1)
        expect(
            client.post(f"/workflows/{NAME}/versions", headers=designer, json={"text": second}),
            201,
            "save the second version",
        )
        expect(
            client.post(f"/workflows/{NAME}/versions/2/publish", headers=designer),
            200,
            "publish v2",
        )
        # two campaigns, so that the page Campaigns has something to show: the quarter of the last
        # run (collecting is closed and the forms are being validated) and the quarter of the live
        # demonstration, which is still collecting
        line.set(now - timedelta(minutes=15))
        admin = bearer(tokens["admin"])
        for name, year, quarter, days, path in CAMPAIGNS:
            made = client.post(
                "/campaigns",
                headers=admin,
                json={
                    "name": name,
                    "year": year,
                    "quarter": quarter,
                    "regions": [r["code"] for r in regions],
                    "deadline": (now + timedelta(days=days)).date().isoformat(),
                    "workflow": NAME,
                    "workflow_version": 2,
                    "rules": "regional-indicators-v1",
                },
            )
            expect(made, 201, f"make the campaign {name}")
            for to in path:
                expect(
                    client.post(
                        f"/campaigns/{made.json()['id']}/transitions",
                        headers=admin,
                        json={"to": to},
                    ),
                    200,
                    f"move the campaign {name} to {to}",
                )
        line.set(now)
    finally:
        client.close()
        gc.collect()  # a connection left to the collector keeps the file locked on Windows
    return {
        "runs": len(PLAN),
        "campaigns": len(CAMPAIGNS),
        "quarters": sorted({i.quarter for i in PLAN}),
        "started_by": sorted(set(started_by)),
    }
