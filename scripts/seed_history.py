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


def open_client(
    work: Path, db: str, now: datetime, timeline: Timeline | None = None, seed: int = 52
) -> TestClient:
    """The API of the core in this process, on the files of ``work``, with the clock of the history.

    Runs are driven before the reply (there is no pool), identifiers come from a generator with a
    seed, so the same call gives the same history.
    """
    line = timeline or Timeline(now)
    rng = random.Random(seed)  # another seed for another story, or the identifiers would collide
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


# the region whose form of the quarter keeps a mistake: one problem stays open in the demonstration
RELATION_IN = "JAL"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def send_form(
    client: TestClient,
    token: str,
    region: str,
    quarter: str,
    work: Path,
    mistake: bool,
    households_too_many: bool = False,
) -> None:
    """One form of a region sent through the API, as an operator does on the page."""
    path = work / "send.xlsx"
    rows = make_forms.rows_of(region, mistake=mistake)
    if (
        households_too_many
    ):  # more households than people: a mistake of the relation, not of a range
        rows[1] = (rows[1][0], rows[1][1], rows[0][2] * 10)
    make_forms.write_form(path, rows)
    reply = client.put(
        f"/forms/{region}/{quarter}",
        content=path.read_bytes(),
        headers={**bearer(token), "Content-Type": XLSX},
    )
    expect(reply, 200, f"send the form of {region} for {quarter}")


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


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def expect(reply: Any, status: int, what: str) -> None:
    if reply.status_code != status:
        raise RuntimeError(f"the history could not {what}: {reply.status_code} {reply.text[:300]}")


# -- the campaign with the cases of the cycle (US-63) --------------------------------------------
CYCLE_NAME = "Quarterly Demographic Report, Q2 2025"
CYCLE_PERIOD = "2025Q2"
CYCLE_RULES = "regional-demography-v2"
# region -> (the case of its first form, the key of the user who sends it)
CYCLE_FORMS = {
    "BIS": ("good", "operator"),
    "OSC": ("good", "operator"),
    "ISK": ("good", "operator2"),
    "OSH": ("good", "osh"),  # the operator of the office of Osh oblast, when there is one
    "CHU": ("good", "chui"),
    "BAT": ("missing-row", "operator2"),
    "JAL": ("wrong-sum", "operator"),
    "TAL": ("text-for-a-number", "operator2"),
}  # NAR sends nothing
REJECTION = (
    "Talas and Naryn are not in the result: seven regions of nine cannot be published as the"
    " figure of the republic"
)


def seed_campaign(work: Path, db: str, tokens: dict[str, str], now: datetime) -> dict[str, Any]:
    """A campaign that has been through the whole cycle, for the demonstration.

    Eight regions send their form for 2025Q2 and Naryn does not. Three of the forms have a
    mistake, each of another kind (a row that is missing, a total that is not the sum of its
    parts, a text where a number belongs), and each is corrected later, so each has two versions
    and a problem that was resolved. A preliminary result is made from six regions, a final one
    from seven is rejected by the chief statistician with her reason, and a final one from eight
    waits for her decision: that is where the demonstration takes over. The workflow is run from
    the campaign twice, before and after the last correction.

    It can be called after ``seed`` or alone; the users of ``tokens`` must exist in ``db``. The
    regional operators (``osh``, ``chui``) send the forms of their offices when they exist.
    """
    now = now.astimezone(UTC)
    client = open_client(work, db, now, seed=63)
    line: Timeline = client.timeline  # type: ignore[attr-defined]
    admin, designer = bearer(tokens["admin"]), bearer(tokens["designer"])
    chief = bearer(tokens["chief"])

    def user(key: str) -> str:
        return tokens.get(key) or tokens["operator"]

    def send(region: str, case: str, key: str) -> None:
        path = work / "send.xlsx"
        make_forms.write_form(path, make_forms.demography_rows(region, case))
        reply = client.put(
            f"/forms/{region}/{CYCLE_PERIOD}",
            content=path.read_bytes(),
            headers={**bearer(user(key)), "Content-Type": XLSX},
        )
        expect(reply, 200, f"send the form of {region} for {CYCLE_PERIOD}")

    try:
        line.set(now - timedelta(days=2))
        regions = YAML(typ="safe").load(CATALOGUE.read_text(encoding="utf8"))["regions"]
        expect(
            client.put("/catalogue/regions", headers=admin, json={"items": regions}),
            200,
            "load the catalogue",
        )
        listed = client.get(f"/workflows/{NAME}/versions", headers=designer)
        published = (
            [v["number"] for v in listed.json() if v["status"] == "published"]
            if listed.status_code == 200
            else []
        )
        if not published:  # called alone: the workflow of the example, published
            text = EXAMPLE.read_text(encoding="utf8")
            expect(
                client.post(f"/workflows/{NAME}/versions", headers=designer, json={"text": text}),
                201,
                "save the workflow",
            )
            expect(
                client.post(f"/workflows/{NAME}/versions/1/publish", headers=designer),
                200,
                "publish",
            )
            published = [1]
        made = client.post(
            "/campaigns",
            headers=admin,
            json={
                "name": CYCLE_NAME,
                "year": 2025,
                "quarter": 2,
                "regions": [r["code"] for r in regions],
                "deadline": (now + timedelta(days=3)).date().isoformat(),
                "workflow": NAME,
                "workflow_version": published[-1],
                "rules": CYCLE_RULES,
                "preliminary_min": 5,
                "final_min": 7,
            },
        )
        expect(made, 201, f"make the campaign {CYCLE_NAME}")
        cid = made.json()["id"]

        def move(to: str) -> None:
            reply = client.post(f"/campaigns/{cid}/transitions", headers=admin, json={"to": to})
            expect(reply, 200, f"move the campaign to {to}")

        def result(kind: str, key: str = "operator") -> int:
            reply = client.post(
                f"/campaigns/{cid}/consolidations", headers=bearer(user(key)), json={"kind": kind}
            )
            expect(reply, 201, f"make a {kind} result")
            return int(reply.json()["number"])

        def run() -> None:
            reply = client.post(f"/campaigns/{cid}/runs", headers=bearer(user("operator")))
            expect(reply, 202, "run the workflow of the campaign")

        move("OPEN")
        move("COLLECTING")
        # the first forms: five good ones and three with a mistake, each of another kind
        line.set(now - timedelta(hours=5))
        for region, (case, key) in CYCLE_FORMS.items():
            send(region, case, key)
        # Batken sends the row that was missing; six forms are valid
        line.set(now - timedelta(hours=4))
        send("BAT", "good", "operator2")
        move("VALIDATING")
        result("PRELIMINARY")  # 1: six of nine, incomplete
        run()
        # Jalal-Abad corrects its sum: the preliminary result is stale, the campaign collects again
        line.set(now - timedelta(hours=3))
        send("JAL", "good", "operator")
        move("VALIDATING")
        second = result("FINAL")  # 2: seven of nine, what the policy asks for
        line.set(now - timedelta(hours=2, minutes=30))
        rejected = client.post(
            f"/campaigns/{cid}/consolidations/{second}/decision",
            headers=chief,
            json={"decision": "REJECT", "comment": REJECTION},
        )
        expect(rejected, 200, "reject the second result")
        # the collecting is opened again and Talas sends its births as a number
        line.set(now - timedelta(hours=1))
        move("COLLECTING")
        send("TAL", "good", "operator2")
        move("VALIDATING")
        run()
        line.set(now - timedelta(minutes=30))
        third = result("FINAL")  # 3: eight of nine; it waits for the chief statistician
        line.set(now)
    finally:
        client.close()
        gc.collect()  # a connection left to the collector keeps the file locked on Windows
    return {"campaign": cid, "period": CYCLE_PERIOD, "results": third, "name": CYCLE_NAME}


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
        # the forms of the quarter that is being validated, sent through the API so that each has a
        # version: the region BAT sent a form with a mistake first and a corrected one an hour
        # later, so the page Forms has a history to show
        line.set(now - timedelta(hours=3))
        for region in make_forms.REGIONS:
            mistaken = region == make_forms.MISTAKE_IN
            send_form(
                client, tokens["operator"], region, "2024Q4", work, mistake=mistaken,
                households_too_many=region == RELATION_IN,
            )  # fmt: skip
        line.set(now - timedelta(hours=2))
        send_form(client, tokens["operator2"], make_forms.MISTAKE_IN, "2024Q4", work, mistake=False)
        # the error of BAT became a problem when its first form came, and its second form resolved
        # it; the error of JAL was never corrected, so one problem is still open and the campaign
        # has eight valid forms and one with an error
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
