"""Render the PlantUML sources of docs/diagrams/ to SVG.

Usage: python scripts/render_diagrams.py [--dest DIR] [--check] [FILE ...]

The jar is found by the environment variable PLANTUML_JAR, or else a ``plantuml`` command on the
PATH. Java and Graphviz (``dot``) must be installed. Without FILE, every ``*.puml`` of
docs/diagrams/ is rendered into docs/diagrams/svg/. With ``--check`` nothing is written to the
folder of the pictures: the files are rendered into a temporary folder and the script says which
pictures are missing from the destination.

The sources are the record; the pictures are committed so that the report can show them, and they
are made again with this script whenever a source changes.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "docs" / "diagrams"
DEFAULT_DEST = SOURCES / "svg"


def command() -> list[str] | None:
    """The command that runs PlantUML, or None if there is none."""
    jar = os.environ.get("PLANTUML_JAR")
    if jar and Path(jar).is_file() and shutil.which("java"):
        return ["java", "-Djava.awt.headless=true", "-jar", jar]
    found = shutil.which("plantuml")
    return [found] if found else None


def sources() -> list[Path]:
    return sorted(SOURCES.glob("*.puml"))


def render(files: list[Path], dest: Path) -> None:
    run = command()
    if run is None:
        raise SystemExit("no PlantUML: set PLANTUML_JAR to the jar or put plantuml on the PATH")
    dest.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(
        [*run, "-tsvg", "-failonerror", "-o", str(dest.resolve()), *map(str, files)],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise SystemExit(f"PlantUML failed:\n{done.stdout}{done.stderr}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    files = [f.resolve() for f in args.files] or sources()
    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            render(files, Path(tmp))
        missing = [f.stem for f in files if not (args.dest / f"{f.stem}.svg").is_file()]
        print("missing pictures: " + (", ".join(missing) if missing else "none"))
        return 1 if missing else 0
    render(files, args.dest)
    for f in files:
        print(f"{f.name} -> {args.dest / (f.stem + '.svg')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
