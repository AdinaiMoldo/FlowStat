"""What the apps need from outside: the database, the data directory, the clock.

The functions that read the real clock and make identifiers are passed in by the executable that
starts the server (``flowstat.api.__main__``); the apps never read them themselves.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AppConfig:
    db_path: str
    data_dir: Path
    clock: Callable[[], str]  # ISO 8601 text, for the stored times
