"""Single source of truth for the architecture rules (docs/architecture.md)."""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterator
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "flowstat"

# Layer of every package. A module may import from a package on a strictly lower layer
# or from its own package. Packages on the same layer must not import each other.
LAYERS: dict[str, int] = {
    "model": 0,
    "dsl": 1,
    "solver": 2,
    "repo": 2,
    "compiler": 3,
    "analysis": 4,
    "engine": 5,
    "actions": 6,
    "recovery": 6,
    "migration": 6,
    "service": 7,
    "api": 8,
    "deploy": 8,
    "serve": 9,
}

# C1-C6 packages: no third-party library except the explicit exceptions below.
STRICT_PACKAGES = {
    "model",
    "dsl",
    "solver",
    "compiler",
    "analysis",
    "engine",
    "recovery",
    "migration",
}

# Third-party top-level name -> the files (relative to src/flowstat) that may import it.
ALLOWED_THIRD_PARTY: dict[str, set[str]] = {
    "ruamel": {"dsl/reader.py"},
}


def python_files() -> Iterator[Path]:
    yield from sorted(SRC.rglob("*.py"))


def package_of(path: Path) -> str | None:
    rel = path.relative_to(SRC)
    return rel.parts[0] if len(rel.parts) > 1 else None


def imports_of(path: Path) -> Iterator[tuple[str, int, int]]:
    """Yield (module name, level, line) for every import statement of a file."""
    tree = ast.parse(path.read_text(encoding="utf8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, 0, node.lineno
        elif isinstance(node, ast.ImportFrom):
            yield node.module or "", node.level, node.lineno


def is_stdlib(top: str) -> bool:
    return top in sys.stdlib_module_names or top == "__future__"
