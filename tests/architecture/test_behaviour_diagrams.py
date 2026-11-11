"""The activity and sequence diagrams of C1 to C6 against their cards and the code (DOC-9).

Every function C1 to C6 has an activity diagram and a sequence diagram. Each names the section of
its algorithm card that it follows (a ``' Card:`` line, and the section must exist), the code it
draws (a ``' code:`` line, and the function must exist), and the activity diagram contains every
procedure that the card's pseudocode defines, so that a step added to the card is missing from the
picture until the picture has it.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DIAGRAMS = ROOT / "docs" / "diagrams"
CARDS = ROOT / "docs" / "algorithms"
FUNCTIONS = ("c1", "c2", "c3", "c4", "c5", "c6")
KINDS = ("activity", "sequence")
ALL = [f"{c}-{k}" for c in FUNCTIONS for k in KINDS]


def source(name: str) -> str:
    return (DIAGRAMS / f"{name}.puml").read_text(encoding="utf8")


def slug(heading: str) -> str:
    """The anchor of a heading as the repository's page makes it."""
    text = re.sub(r"[^\w\s-]", "", heading.strip().lower())
    return re.sub(r"\s", "-", text)


def anchors(card: Path) -> set[str]:
    found: set[str] = set()
    for line in card.read_text(encoding="utf8").splitlines():
        match = re.match(r"^#{1,6} (.+)$", line)
        if match:
            found.add(slug(match.group(1)))
    return found


def procedures(card: Path, section: str) -> set[str]:
    """The names the pseudocode of a section of a card defines (``name(...):`` at line start)."""
    lines = card.read_text(encoding="utf8").splitlines()
    start = next(i for i, line in enumerate(lines) if slug(line.lstrip("# ")) == section)
    names: set[str] = set()
    inside = False
    for line in lines[start + 1 :]:
        if line.startswith("#"):
            break
        if line.startswith("```"):
            inside = not inside
        elif inside:
            match = re.match(r"^([a-z_]+)(?:\([^)]*\))?(?: when [^:]*)?:", line)
            if match:
                names.add(match.group(1))
    return names


def test_there_are_twelve_behaviour_diagrams() -> None:
    assert len(ALL) == 12
    assert {p.stem for p in DIAGRAMS.glob("c[1-6]-*.puml")} == set(ALL)


@pytest.mark.parametrize("name", ALL)
def test_a_behaviour_diagram_is_well_formed_and_has_its_picture(name: str) -> None:
    lines = source(name).strip().splitlines()
    assert lines[0] == f"@startuml {name}" and lines[-1] == "@enduml"
    assert (DIAGRAMS / "svg" / f"{name}.svg").is_file(), "run scripts/render_diagrams.py"


@pytest.mark.parametrize("name", ALL)
def test_it_names_the_sections_of_its_card_and_they_exist(name: str) -> None:
    cards = re.findall(r"^' Card: (docs/algorithms/C\d\.md)#([\w-]+)\s*$", source(name), re.M)
    assert cards, f"{name} does not say which section of a card it follows"
    for path, anchor in cards:
        assert path == f"docs/algorithms/{name[:2].upper()}.md", f"{name} follows another card"
        assert anchor in anchors(ROOT / path), f"{path} has no section #{anchor}"


@pytest.mark.parametrize("name", ALL)
def test_every_function_it_names_exists_in_the_code(name: str) -> None:
    named = re.findall(r"^' code: ([\w.]+):([\w.]+)\s*$", source(name), re.M)
    assert named, f"{name} names no code"
    for module, attribute in named:
        found: object = importlib.import_module(module)
        for part in attribute.split("."):
            assert hasattr(found, part), f"{module}:{attribute} does not exist"
            found = getattr(found, part)
        assert callable(found), f"{module}:{attribute} is not a function or a class"


@pytest.mark.parametrize("function", FUNCTIONS)
def test_the_activity_diagram_has_every_procedure_of_the_pseudocode_of_its_card(
    function: str,
) -> None:
    text = source(f"{function}-activity")
    card = CARDS / f"{function.upper()}.md"
    missing: list[str] = []
    for _, anchor in re.findall(r"^' Card: (\S+)#([\w-]+)$", text, re.M):
        for name in sorted(procedures(card, anchor)):
            if not re.search(rf"\b{name}\b", re.sub(r"(?m)^'.*$", "", text)):
                missing.append(f"{anchor}: {name}")
    assert not missing, missing


def test_the_cards_with_pseudocode_define_the_procedures_the_diagrams_check() -> None:
    """The check above is not empty: these are the procedures it looks for."""
    assert procedures(CARDS / "C3.md", "pseudocode") == {"start", "decide", "advance", "finish"}
    assert procedures(CARDS / "C4.md", "pseudocode") == {"commit", "restore", "resolve", "blockers"}
    assert procedures(CARDS / "C5.md", "pseudocode") == {"check"}
    assert procedures(CARDS / "C5.md", "pseudocode-part-2") == {"move", "forward", "recover"}
    assert procedures(CARDS / "C6.md", "pseudocode") == {"check", "solve_clause", "generate"}
