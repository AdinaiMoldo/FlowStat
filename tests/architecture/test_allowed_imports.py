"""C1-C6 packages use the standard library and our own code; the only exception is the YAML reader.

The allow-list lives in tests/architecture/rules.py and is mirrored in docs/reuse.md.
A change to it needs a journal entry (docs/journal.md).
"""

from __future__ import annotations

from tests.architecture.rules import (
    ALLOWED_THIRD_PARTY,
    SRC,
    STRICT_PACKAGES,
    imports_of,
    is_stdlib,
    package_of,
    python_files,
)


def test_strict_packages_import_only_stdlib_and_flowstat() -> None:
    violations: list[str] = []
    for path in python_files():
        if package_of(path) not in STRICT_PACKAGES:
            continue
        rel = path.relative_to(SRC).as_posix()
        for module, level, line in imports_of(path):
            if level > 0:
                continue
            top = module.split(".")[0]
            if top == "flowstat" or is_stdlib(top):
                continue
            if rel in ALLOWED_THIRD_PARTY.get(top, set()):
                continue
            violations.append(f"{rel}:{line}: third-party import '{top}' is not on the allow-list")
    assert not violations, "\n".join(violations)
