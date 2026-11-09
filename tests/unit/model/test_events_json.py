"""An event as plain data (JSON) and back: the form a service ships to the core (US-31)."""

from __future__ import annotations

import json

import pytest

from flowstat.model.events import Event, EventKind, event_from_dict, event_to_dict
from flowstat.model.states import RunOutcome, StepState

EVERY_FIELD = Event(
    7,
    EventKind.STEP_RESOLVED,
    12.5,
    step="a",
    state=StepState.SUCCEEDED,
    outcome=RunOutcome.SUCCEEDED,
    code="E-X",
    message="m",
    output={"rows": [1, 2], "ok": True, "n": None},
    attempt=2,
    delay=0.5,
    decision="retry",
)


def test_every_kind_of_event_survives_the_trip_through_json() -> None:
    for kind in EventKind:
        event = Event(3, kind, 1.25)
        assert event_from_dict(json.loads(json.dumps(event_to_dict(event)))) == event


def test_an_event_with_every_field_survives_the_trip() -> None:
    assert event_from_dict(json.loads(json.dumps(event_to_dict(EVERY_FIELD)))) == EVERY_FIELD


def test_the_form_is_plain_names_and_values() -> None:
    data = event_to_dict(EVERY_FIELD)
    assert data["kind"] == "STEP_RESOLVED" and data["state"] == "SUCCEEDED"
    assert data["outcome"] == "SUCCEEDED" and data["seq"] == 7 and data["at"] == 12.5
    assert event_to_dict(Event(0, EventKind.RUN_STARTED, 0.0))["state"] is None


@pytest.mark.parametrize(
    "broken",
    [
        {},
        {"seq": 0, "kind": "NOT_A_KIND", "at": 0.0},
        {"seq": "0", "kind": "RUN_STARTED", "at": 0.0},
        {"seq": -1, "kind": "RUN_STARTED", "at": 0.0},
        {"seq": 0, "kind": "RUN_STARTED", "at": "now"},
        {"seq": 0, "kind": "STEP_FINISHED", "at": 0.0, "state": "DONE"},
        {"seq": 0, "kind": "RUN_FINISHED", "at": 0.0, "outcome": "WON"},
        {"seq": 0, "kind": "RUN_STARTED", "at": 0.0, "output": [1]},
        {"seq": 0, "kind": "RUN_STARTED", "at": 0.0, "attempt": "1"},
        {"seq": 0, "kind": "RUN_STARTED", "at": 0.0, "step": 5},
        {"seq": True, "kind": "RUN_STARTED", "at": 0.0},
        {"seq": 0, "kind": "RUN_STARTED", "at": 0.0, "surprise": 1},
    ],
)
def test_data_that_is_not_an_event_is_refused(broken: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="event"):
        event_from_dict(broken)
