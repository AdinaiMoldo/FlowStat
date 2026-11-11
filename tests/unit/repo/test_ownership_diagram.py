"""The ownership diagram of docs/diagrams/ against the stores (DOC-9).

Every move of the ownership of a run that the stores make in a handover is an arrow of
``ownership-states.puml``, and every arrow is made by one of the calls below.
"""

from __future__ import annotations

import re
from pathlib import Path

from flowstat.model.events import Event, EventKind
from flowstat.model.states import StepState
from flowstat.repo.db import open_database
from flowstat.repo.ownership import OwnershipStore
from flowstat.repo.runs import RunStore, StoredRun

ROOT = Path(__file__).resolve().parents[3]
T = "2025-01-01T00:00:00"


def history() -> list[Event]:
    return [
        Event(0, EventKind.RUN_STARTED, 0.0),
        Event(1, EventKind.STEP_STARTED, 1.0, step="a"),
        Event(2, EventKind.STEP_FINISHED, 2.0, step="a", state=StepState.SUCCEEDED, output={}),
    ]


def store() -> tuple[RunStore, OwnershipStore]:
    db = open_database(":memory:")
    runs = RunStore(db)
    runs.create("r1", "demo", 1, {}, T)
    runs.append("r1", history())
    return runs, OwnershipStore(db)


def drawn() -> set[tuple[str, str]]:
    source = (ROOT / "docs" / "diagrams" / "ownership-states.puml").read_text(encoding="utf8")
    return set(re.findall(r"^(\w+) --> (\w+)", source, re.M))


def made() -> set[tuple[str, str]]:
    moves: set[tuple[str, str]] = set()

    def step(owners: OwnershipStore, call: object) -> None:
        before = owners.get("r1").state.value
        assert callable(call)
        call()
        after = owners.get("r1").state.value
        moves.add((before, after))

    runs, owners = store()
    moved = StoredRun("r1", "demo", 2, {}, T)
    step(owners, lambda: owners.fence("r1", 0, 2, "other", 2))  # OWNED -> MIGRATING
    step(owners, lambda: owners.release("r1", 1))  # MIGRATING -> MIGRATED_OUT
    step(owners, lambda: runs.adopt(moved, history(), 2, "other", 9.0))  # -> OWNED, it came back
    runs2, owners2 = store()
    step(owners2, lambda: runs2.retarget("r1", 0, 2, 2, {}, 5.0))  # OWNED -> OWNED
    return moves


def test_every_move_of_the_ownership_in_a_handover_is_an_arrow_of_the_diagram() -> None:
    assert made() - drawn() == set()


def test_every_arrow_of_the_ownership_diagram_is_made_by_a_call_of_the_stores() -> None:
    assert drawn() - made() == set()
