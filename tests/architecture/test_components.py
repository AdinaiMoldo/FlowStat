"""Where the libraries of category B and the calls into the operating system may be used.

docs/architecture.md: components of categories A and B live in ``api``, ``deploy``, ``actions`` and
``repo``. NFR-07 (portability): no operating-system calls outside the packager, so ``subprocess``,
``socket`` and ``docker`` are imported only in ``deploy``.
"""

from __future__ import annotations

from tests.architecture.rules import SRC, imports_of, package_of, python_files

# top-level module -> the packages of src/flowstat that may import it
WHERE: dict[str, set[str]] = {
    "fastapi": {"api"},
    "starlette": {"api"},
    "pydantic": {"api"},
    "uvicorn": {"api", "deploy"},
    "jinja2": {"deploy"},
    "docker": {"deploy"},
    "subprocess": {"deploy"},
    "socket": {"deploy"},
    "openpyxl": {"actions"},
}


def test_libraries_and_system_calls_are_used_only_where_the_architecture_says() -> None:
    violations: list[str] = []
    for path in python_files():
        package = package_of(path)
        rel = path.relative_to(SRC).as_posix()
        for module, level, line in imports_of(path):
            if level > 0:
                continue
            top = module.split(".")[0]
            allowed = WHERE.get(top)
            if allowed is not None and package not in allowed:
                where = ", ".join(sorted(allowed))
                violations.append(f"{rel}:{line}: '{top}' may be imported only in {where}")
    assert not violations, "\n".join(violations)
