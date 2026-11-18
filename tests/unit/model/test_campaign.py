"""US-53: the life cycle of a campaign is a table, and the settings are checked."""

from __future__ import annotations

import pytest

from flowstat.model import campaign as life
from flowstat.model.campaign import CampaignError, Settings

CODES = {"BIS", "OSC", "BAT", "JAL", "ISK", "NAR", "OSH", "TAL", "CHU"}


def settings(**changes: object) -> Settings:
    base: dict[str, object] = {
        "name": "Quarterly Regional Statistics, Q3 2026",
        "year": 2026,
        "quarter": 3,
        "regions": tuple(sorted(CODES)),
        "deadline": "2026-10-31",
        "workflow": "quarterly_consolidation",
        "workflow_version": 1,
    }
    base.update(changes)
    return Settings(**base)  # type: ignore[arg-type]


def test_the_table_has_every_status_and_every_status_can_be_reached_from_a_draft() -> None:
    assert set(life.EDGES) == set(life.STATUSES)
    seen, todo = {life.DRAFT}, [life.DRAFT]
    while todo:
        for to in life.EDGES[todo.pop()]:
            if to not in seen:
                seen.add(to)
                todo.append(to)
    assert seen == set(life.STATUSES)


def test_an_archived_campaign_goes_nowhere_and_nothing_goes_back_to_a_draft() -> None:
    assert life.EDGES[life.ARCHIVED] == {}
    assert all(life.DRAFT not in edges for edges in life.EDGES.values())


def test_no_person_can_approve_or_make_a_result() -> None:
    """The changes that follow from a consolidation or an approval belong to the system."""
    for current, edges in life.EDGES.items():
        for to in (life.READY, life.PRELIMINARY, life.PENDING, life.APPROVED):
            if to in edges:
                assert edges[to] == life.SYSTEM, (current, to)
    assert life.EDGES[life.PENDING][life.REJECTED] == life.SYSTEM


def test_a_person_moves_a_draft_to_open_and_the_system_does_not_need_to() -> None:
    assert life.move(life.DRAFT, life.OPEN, life.MANUAL) == life.OPEN
    with pytest.raises(CampaignError) as error:
        life.move(life.DRAFT, life.OPEN, life.SYSTEM)
    assert error.value.code == "E-CAMP-003"


def test_a_change_the_system_makes_is_refused_to_a_person_with_its_own_code() -> None:
    with pytest.raises(CampaignError) as error:
        life.move(life.VALIDATING, life.READY, life.MANUAL)
    assert error.value.code == "E-CAMP-003"
    assert life.move(life.VALIDATING, life.READY, life.SYSTEM) == life.READY


@pytest.mark.parametrize(
    ("current", "to"),
    [
        (life.DRAFT, life.APPROVED),
        (life.OPEN, life.FINALIZED),
        (life.FINALIZED, life.COLLECTING),
        (life.ARCHIVED, life.DRAFT),
        (life.APPROVED, life.COLLECTING),
        (life.COLLECTING, "NONSENSE"),
    ],
)
def test_a_change_that_is_not_in_the_table_is_refused(current: str, to: str) -> None:
    with pytest.raises(CampaignError) as error:
        life.move(current, to, life.MANUAL)
    assert error.value.code == "E-CAMP-002"


def test_what_a_person_may_ask_for_now() -> None:
    assert life.manual_moves(life.DRAFT) == [life.OPEN, life.ARCHIVED]
    assert life.manual_moves(life.PENDING) == []  # only the decision of the approver moves it
    assert life.manual_moves(life.ARCHIVED) == []


def test_the_forms_of_a_period_are_locked_once_the_result_is_approved() -> None:
    assert {life.APPROVED, life.FINALIZED, life.ARCHIVED} == life.LOCKED


def test_the_thresholds_default_to_about_three_quarters_and_all() -> None:
    assert life.default_thresholds(9) == (7, 9)
    assert life.default_thresholds(1) == (1, 1)
    done = life.checked(settings(), CODES)
    assert (done.preliminary_min, done.final_min) == (7, 9)
    assert done.period == "2026Q3"


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"name": "  "}, "name"),
        ({"name": "x" * 81}, "name"),
        ({"year": 1999}, "year"),
        ({"quarter": 5}, "quarter"),
        ({"rules": "strict"}, "rules"),
        ({"regions": ()}, "regions"),
        ({"regions": ("BIS", "BIS")}, "regions"),
        ({"regions": ("BIS", "NOWHERE")}, "regions"),
        ({"regions": ("../etc",)}, "regions"),
        ({"deadline": "next week"}, "deadline"),
        ({"deadline": "2026-02-30"}, "deadline"),
        ({"workflow": " "}, "workflow"),
        ({"workflow_version": 0}, "workflow"),
        ({"approver": ""}, "approver"),
        ({"preliminary_min": 0}, "final_min"),
        ({"preliminary_min": 8, "final_min": 7}, "final_min"),
        ({"final_min": 10}, "final_min"),
    ],
)
def test_settings_that_are_wrong_are_refused_and_name_the_field(
    changes: dict[str, object], field: str
) -> None:
    with pytest.raises(CampaignError) as error:
        life.checked(settings(**changes), CODES)
    assert error.value.code == "E-CAMP-001" and error.value.field == field


def test_a_smaller_set_of_regions_gets_thresholds_of_its_own_size() -> None:
    done = life.checked(settings(regions=("BIS", "OSC", "BAT", "JAL")), CODES)
    assert (done.preliminary_min, done.final_min) == (3, 4)
