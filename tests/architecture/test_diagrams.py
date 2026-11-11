"""The structure diagrams of docs/diagrams/ against the code (DOC-8).

A diagram that disagrees with the code is worse than none, so what can be compared is compared:
every arrow of the component diagram is a real import between packages and every real import is an
arrow; every class, field and operation of the class diagram exists; every module that the
deployment diagram starts exists.
"""

from __future__ import annotations

import dataclasses
import importlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.architecture.rules import LAYERS, imports_of, package_of, python_files

ROOT = Path(__file__).resolve().parents[2]
DIAGRAMS = ROOT / "docs" / "diagrams"
STRUCTURE = ("use-case", "context", "class", "component", "deployment")


def text(name: str) -> str:
    return (DIAGRAMS / f"{name}.puml").read_text(encoding="utf8")


def actual_edges() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in python_files():
        here = package_of(path)
        if here is None:
            continue
        for module, level, _ in imports_of(path):
            parts = module.split(".")
            if level == 0 and parts[0] == "flowstat" and len(parts) > 1:
                there = parts[1]
                if there != here and there in LAYERS:
                    found.add((here, there))
    return found


# -- every source is a well-formed file with the name of its picture -----------------------------
@pytest.mark.parametrize("name", STRUCTURE)
def test_a_structure_diagram_exists_and_is_named_like_its_file(name: str) -> None:
    source = text(name).strip().splitlines()
    assert source[0] == f"@startuml {name}" and source[-1] == "@enduml"


@pytest.mark.parametrize("name", STRUCTURE)
def test_every_source_has_its_picture(name: str) -> None:
    picture = DIAGRAMS / "svg" / f"{name}.svg"
    assert picture.is_file(), f"run scripts/render_diagrams.py: {picture.name} is missing"
    assert picture.read_text(encoding="utf8").lstrip().startswith("<?xml")


# -- component diagram --------------------------------------------------------------------------
def test_the_component_diagram_draws_every_package_and_no_other() -> None:
    drawn = re.findall(r"^\s*component (\w+)\s*$", text("component"), re.M)
    assert sorted(drawn) == sorted(LAYERS)


def test_the_component_diagram_puts_every_package_on_its_layer() -> None:
    source = text("component")
    for block in re.finditer(r'package "layer (\d)" \{(.*?)\n\}', source, re.S):
        for package in re.findall(r"component (\w+)", block.group(2)):
            assert LAYERS[package] == int(block.group(1)), package


def test_the_arrows_of_the_component_diagram_are_the_imports_between_packages() -> None:
    drawn = set(re.findall(r"^(\w+) --> (\w+)\s*$", text("component"), re.M))
    real = actual_edges()
    assert drawn - real == set(), "an arrow without an import"
    assert real - drawn == set(), "an import without an arrow"
    assert len(drawn) == len(re.findall(r"^\w+ --> \w+\s*$", text("component"), re.M))  # no twice


def test_no_arrow_of_the_component_diagram_points_up() -> None:
    for a, b in re.findall(r"^(\w+) --> (\w+)\s*$", text("component"), re.M):
        assert LAYERS[a] > LAYERS[b], f"{a} -> {b}"


# -- class diagram ------------------------------------------------------------------------------
CLASS = re.compile(r'^\s*(class|enum) (?:"(\w+)" as (\w+)|(\w+))\s*(\{)?\s*$')


def drawn_classes() -> list[tuple[str, str, str, str, list[str]]]:
    """(kind, module, name in code, name in the picture, members) of every class drawn."""
    found: list[tuple[str, str, str, str, list[str]]] = []
    module = ""
    lines = text("class").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        opened = re.match(r'^package "([\w.]+)" \{\s*$', line)
        if opened:
            module = opened.group(1)
        match = CLASS.match(line)
        if match:
            kind, quoted, alias, plain, brace = match.groups()
            members: list[str] = []
            if brace:
                i += 1
                while lines[i].strip() != "}":
                    members.append(lines[i].strip())
                    i += 1
            found.append((kind, module, quoted or plain, alias or plain, members))
        i += 1
    return found


def test_the_class_diagram_is_not_empty_and_names_are_unique() -> None:
    drawn = drawn_classes()
    assert len(drawn) >= 25
    aliases = [alias for _, _, _, alias, _ in drawn]
    assert len(aliases) == len(set(aliases))


def test_every_class_of_the_class_diagram_exists_in_the_code() -> None:
    for kind, module, name, _, _ in drawn_classes():
        assert importlib.util.find_spec(module) is not None, f"no module {module}"
        cls = getattr(importlib.import_module(module), name, None)
        assert isinstance(cls, type), f"{module}.{name} is not a class"
        assert (kind == "enum") == hasattr(cls, "__members__"), f"{name}: class or enum?"


def test_every_field_and_operation_of_the_class_diagram_exists() -> None:
    for _, module, name, _, members in drawn_classes():
        cls = getattr(importlib.import_module(module), name)
        fields = (
            {f.name for f in dataclasses.fields(cls)} if dataclasses.is_dataclass(cls) else set()
        )
        for member in members:
            match = re.fullmatch(r"\+(\w+)(\(\))?", member)
            assert match, f"{name}: cannot read '{member}'"
            attribute, operation = match.group(1), match.group(2)
            if operation:
                assert callable(getattr(cls, attribute, None)), f"{name}.{attribute}()"
            else:
                assert attribute in fields, f"{name}.{attribute} is not a field"


def test_every_relation_of_the_class_diagram_joins_two_drawn_classes() -> None:
    aliases = {alias for _, _, _, alias, _ in drawn_classes()}
    relation = re.compile(r"^(\w+) (?:-->|\.\.>|\*--|o--) (\w+)(?: : .*)?$", re.M)
    found = relation.findall(text("class"))
    assert len(found) >= 20
    for a, b in found:
        assert a in aliases and b in aliases, f"{a} -> {b}"


# -- deployment diagram -------------------------------------------------------------------------
def test_every_module_the_deployment_diagram_starts_exists() -> None:
    modules = re.findall(r"python -m ([\w.]+)", text("deployment"))
    assert sorted(modules) == ["flowstat.api.service", "flowstat.serve"]
    for module in modules:
        assert importlib.util.find_spec(module) is not None, module
    assert importlib.util.find_spec("flowstat.serve.__main__") is not None


# -- rendering ----------------------------------------------------------------------------------
def has_plantuml() -> bool:
    jar = os.environ.get("PLANTUML_JAR")
    java = shutil.which("java") is not None
    return java and (bool(jar and Path(jar).is_file()) or shutil.which("plantuml") is not None)


@pytest.mark.skipif(not has_plantuml(), reason="PlantUML is not installed (PLANTUML_JAR)")
def test_every_source_renders_without_an_error(tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "render_diagrams.py"), "--dest", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert sorted(p.stem for p in tmp_path.glob("*.svg")) == sorted(
        p.stem for p in DIAGRAMS.glob("*.puml")
    )
