"""Dependency direction: imports point down the layers, never up and never sideways."""

from __future__ import annotations

from tests.architecture.rules import LAYERS, SRC, imports_of, package_of, python_files


def test_every_package_has_a_layer() -> None:
    packages = {p.name for p in SRC.iterdir() if p.is_dir() and not p.name.startswith("__")}
    assert packages == set(LAYERS), (
        "Every package must be placed on a layer in tests/architecture/rules.py "
        f"(missing: {sorted(packages - set(LAYERS))}, stale: {sorted(set(LAYERS) - packages)})"
    )


def test_imports_point_down() -> None:
    violations: list[str] = []
    for path in python_files():
        pkg = package_of(path)
        if pkg is None:
            continue
        for module, level, line in imports_of(path):
            where = f"{path.relative_to(SRC)}:{line}"
            if level > 0:
                if level > 1:
                    violations.append(
                        f"{where}: relative import above the package, use an absolute import"
                    )
                continue
            parts = module.split(".")
            if parts[0] != "flowstat" or len(parts) < 2:
                continue
            target = parts[1]
            if target == pkg or target not in LAYERS:
                continue
            if LAYERS[target] >= LAYERS[pkg]:
                violations.append(
                    f"{where}: {pkg} (layer {LAYERS[pkg]}) must not import "
                    f"{target} (layer {LAYERS[target]})"
                )
    assert not violations, "\n".join(violations)
