"""Who owns a run (DR-01): at any moment one store, and a number that grows at every move."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OwnerState(Enum):
    OWNED = "OWNED"  # this store drives the run
    MIGRATING = "MIGRATING"  # fenced: the run is on its way to `peer`; no command is accepted
    MIGRATED_OUT = "MIGRATED_OUT"  # `peer` owns it now; the history here is a copy


@dataclass(frozen=True, slots=True)
class Ownership:
    """The ownership of one run in one store. A run without a row is OWNED at epoch 0.

    ``epoch`` grows by one at every handover; ``peer`` is the new owner while MIGRATING or
    MIGRATED_OUT, and the old owner for an OWNED run that came from elsewhere. ``version`` is the
    version the run is moving to (kept so that a crash in the middle can be taken forward).
    """

    run_id: str
    epoch: int = 0
    state: OwnerState = OwnerState.OWNED
    peer: str | None = None
    version: int | None = None

    @property
    def owned(self) -> bool:
        return self.state is OwnerState.OWNED
