"""Make the bundle of a published version (docs/api-spec.md section 7)."""

from __future__ import annotations

import importlib.metadata
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

import jinja2

import flowstat
from flowstat.model.versions import Version
from flowstat.service.bundle import (
    CATALOGUE,
    WORKFLOW,
    Manifest,
    encode,
    sha256_bytes,
    write_manifest,
)

# What the service imports besides the standard library; pinned to the versions that ran the tests.
REQUIREMENTS = ("ruamel.yaml", "openpyxl", "fastapi", "pydantic", "uvicorn")


def _pins() -> list[str]:
    return [f"{name}=={importlib.metadata.version(name)}" for name in REQUIREMENTS]


def render_dockerfile(workflow: str, version: int, python: str, requirements: list[str]) -> str:
    environment = jinja2.Environment(
        loader=jinja2.PackageLoader("flowstat.deploy", "templates"),
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )
    return environment.get_template("Dockerfile.j2").render(
        workflow=workflow, version=version, python=python, requirements=requirements
    )


def build_bundle(
    root: Path,
    service_id: str,
    version: Version,
    api_key_sha256: str,
    title_keys_sha256: Mapping[str, str],
    now: str,
    kind: str,
    control_key_sha256: str = "",
) -> Path:
    """Write ``<root>/<service_id>/`` for ``version`` and return it.

    The text and the catalogue snapshot are written byte for byte and named by their SHA-256 in
    the manifest. A container needs the Dockerfile and a copy of the package; a process does not.
    """
    bundle = root / service_id
    bundle.mkdir(parents=True, exist_ok=False)  # a second bundle with the same id is a bug
    (bundle / WORKFLOW).write_bytes(encode(version.text))
    (bundle / CATALOGUE).write_bytes(encode(version.catalogue))
    (bundle / "data").mkdir()
    write_manifest(
        bundle,
        Manifest(
            service_id=service_id,
            workflow=version.name,
            version=version.number,
            text_sha256=sha256_bytes(encode(version.text)),
            catalogue_sha256=sha256_bytes(encode(version.catalogue)),
            api_key_sha256=api_key_sha256,
            created_at=now,
            title_keys_sha256=dict(title_keys_sha256),
            control_key_sha256=control_key_sha256,
        ),
    )
    if kind == "docker":
        python = f"{sys.version_info.major}.{sys.version_info.minor}"
        dockerfile = render_dockerfile(version.name, version.number, python, _pins())
        (bundle / "Dockerfile").write_bytes(encode(dockerfile))
        shutil.copytree(
            Path(flowstat.__file__).parent,
            bundle / "app" / "flowstat",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    return bundle
