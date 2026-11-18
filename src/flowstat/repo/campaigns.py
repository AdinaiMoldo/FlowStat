"""A: the reporting campaigns (US-53). A row is one period of collecting the regional reports."""

from __future__ import annotations

import json
import sqlite3

from flowstat.model.campaign import LOCKED, Campaign, Settings

COLUMNS = (
    "id, name, year, quarter, regions, deadline, workflow, workflow_version, template, rules, "
    "preliminary_min, final_min, approver, status, created_by, created_at, updated_at"
)


class CampaignNotFoundError(LookupError):
    pass


class CampaignNameTakenError(ValueError):
    pass


def _campaign(row: tuple[object, ...]) -> Campaign:
    (cid, name, year, quarter, regions, deadline, workflow, version, template, rules,
     preliminary, final, approver, status, created_by, created_at, updated_at) = row  # fmt: skip
    settings = Settings(
        str(name), int(str(year)), int(str(quarter)), tuple(json.loads(str(regions))),
        str(deadline), str(workflow), int(str(version)), str(template), str(rules),
        int(str(preliminary)), int(str(final)), str(approver),
    )  # fmt: skip
    return Campaign(
        str(cid), settings, str(status), str(created_by), str(created_at), str(updated_at)
    )


class CampaignRepo:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    @staticmethod
    def _values(s: Settings) -> tuple[object, ...]:
        assert s.preliminary_min is not None and s.final_min is not None
        return (
            s.name, s.year, s.quarter, json.dumps(list(s.regions)), s.deadline, s.workflow,
            s.workflow_version, s.template, s.rules, s.preliminary_min, s.final_min, s.approver,
        )  # fmt: skip

    def add(self, campaign_id: str, settings: Settings, status: str, by: str, now: str) -> Campaign:
        try:
            self.db.execute(
                f"INSERT INTO campaigns ({COLUMNS}) VALUES ({', '.join('?' * 17)})",
                (campaign_id, *self._values(settings), status, by, now, now),
            )
        except sqlite3.IntegrityError:
            raise CampaignNameTakenError(settings.name) from None
        return self.get(campaign_id)

    def get(self, campaign_id: str) -> Campaign:
        row = self.db.execute(
            f"SELECT {COLUMNS} FROM campaigns WHERE id = ?", (campaign_id,)
        ).fetchone()
        if row is None:
            raise CampaignNotFoundError(campaign_id)
        return _campaign(row)

    def all(self) -> list[Campaign]:
        rows = self.db.execute(
            f"SELECT {COLUMNS} FROM campaigns ORDER BY year DESC, quarter DESC, name"
        ).fetchall()
        return [_campaign(r) for r in rows]

    def of_period(self, period: str) -> list[Campaign]:
        return [c for c in self.all() if c.settings.period == period]

    def locking(self, period: str) -> Campaign | None:
        """A campaign of the period whose forms may no longer change, if there is one."""
        for found in self.of_period(period):
            if found.status in LOCKED:
                return found
        return None

    def replace(self, campaign_id: str, settings: Settings, now: str) -> Campaign:
        try:
            done = self.db.execute(
                "UPDATE campaigns SET name=?, year=?, quarter=?, regions=?, deadline=?, workflow=?,"
                " workflow_version=?, template=?, rules=?, preliminary_min=?, final_min=?,"
                " approver=?, updated_at=? WHERE id=?",
                (*self._values(settings), now, campaign_id),
            )
        except sqlite3.IntegrityError:
            raise CampaignNameTakenError(settings.name) from None
        if done.rowcount == 0:
            raise CampaignNotFoundError(campaign_id)
        return self.get(campaign_id)

    def set_status(self, campaign_id: str, expected: str, status: str, now: str) -> bool:
        """Change the status only if it is still ``expected``: two people pressing at once do not
        both succeed. False when it was not."""
        done = self.db.execute(
            "UPDATE campaigns SET status=?, updated_at=? WHERE id=? AND status=?",
            (status, now, campaign_id, expected),
        )
        return done.rowcount == 1
