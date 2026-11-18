"""The traceability matrix against the backlog and the repository (DOC-7).

``docs/traceability.md`` has a row for every user story and for every requirement of the Project
Proposal (UR, DR and NFR). Each row says what was done and what was found: a result, or the
reason that there is none (``not verified: ...``). Every file a row names exists, so a renamed
module or test turns the matrix red instead of leaving it wrong.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "docs" / "traceability.md"
REQUIREMENTS = (
    [f"UR-0{i}" for i in range(1, 6)]
    + [f"DR-0{i}" for i in range(1, 5)]
    + [f"NFR-0{i}" for i in range(1, 10)]
)


def rows() -> list[list[str]]:
    """The rows of every table of the matrix, with the six cells of each."""
    found: list[list[str]] = []
    for line in MATRIX.read_text(encoding="utf8").splitlines():
        if not line.startswith("| ") or line.startswith(("| ---", "| UR / DR")):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 6:
            found.append(cells)
    return found


def story_ids(cell: str) -> set[str]:
    """The stories a cell names: ``US-18, US-19``, and ``US-06 to US-12`` as a range."""
    ids = set(re.findall(r"\b(?:US|DOC|DEF)-\d+\b", cell))
    for kind, first, last in re.findall(r"\b(US|DOC|DEF)-(\d+) to (?:US|DOC|DEF)-(\d+)\b", cell):
        ids |= {f"{kind}-{n:02d}" for n in range(int(first), int(last) + 1)}
    return ids


def test_every_story_has_a_row() -> None:
    wanted = {s["id"] for s in json.loads((ROOT / "docs" / "stories.json").read_text("utf8"))}
    named: set[str] = set()
    for row in rows():
        named |= story_ids(row[1])
    assert wanted - named == set(), "stories without a row"
    assert named - wanted == set(), "rows for stories that do not exist"


def test_every_requirement_of_the_proposal_has_a_row() -> None:
    named: set[str] = set()
    for row in rows():
        named |= set(re.findall(r"\b(?:UR|DR|NFR)-\d\d\b", row[0]))
    assert set(REQUIREMENTS) - named == set()


def test_every_row_has_a_result_and_a_result_that_is_not_there_says_why() -> None:
    for row in rows():
        result = row[5]
        assert result, f"{row[0]} / {row[1]} has no result"
        assert result.lower() not in {"todo", "tbd", "pending", "-"}, row
        if result.startswith("not verified"):
            assert re.match(r"not verified: \S+( \S+){4,}", result), f"say why: {row[0]}"


def test_every_row_names_its_evidence() -> None:
    for row in rows():
        assert row[3] and row[4], f"{row[0]} / {row[1]} names no design element or test"


def exists(reference: str) -> bool:
    """A path in the matrix: from the root, or from the package folder, or a test from the root."""
    if any(mark in reference for mark in ("*", "{", "<")):
        return True  # a pattern, not a path
    candidates = [ROOT / reference, ROOT / "src" / "flowstat" / reference]
    return any(c.exists() for c in candidates)


def test_every_file_a_row_names_exists() -> None:
    missing: list[str] = []
    for row in rows():
        for cell in (row[3], row[4]):
            for reference in re.findall(r"`([^`]+)`", cell):
                is_path = re.fullmatch(
                    r"[\w./-]+\.(?:py|js|md|sh|yaml|yml|json|puml|svg)", reference
                )
                if is_path and not exists(reference):
                    missing.append(f"{row[1]}: {reference}")
    assert not missing, "\n".join(missing)


def test_the_negative_check_is_filled_for_every_complex_function() -> None:
    """The table of criteria has a row for C1 to C6 and no empty cell (DOC-7)."""
    text = (ROOT / "docs" / "negative-check.md").read_text(encoding="utf8")
    table = [
        [c.strip() for c in line.strip().strip("|").split("|")]
        for line in text.split("## Criteria check per function", 1)[1].splitlines()
        if line.startswith("| C")
    ]
    assert [row[0].split()[0] for row in table] == ["C1", "C2", "C3", "C4", "C5", "C6"]
    for row in table:
        assert len(row) == 5 and all(row), f"an empty cell in the row of {row[0]}"
        assert row[4].startswith("complex"), row[0]
