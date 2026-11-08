"""Tests of the bundle: the manifest, the frozen files and the refusal of a changed one."""

from __future__ import annotations

from pathlib import Path

import pytest

from flowstat.service.bundle import (
    CATALOGUE,
    FORMAT,
    MANIFEST,
    WORKFLOW,
    BundleError,
    Manifest,
    encode,
    read_bundle,
    sha256_bytes,
    write_manifest,
)

TEXT = "workflow: demo\nversion: 4\r\nsteps: {}\n"  # a carriage return must survive
CATALOGUE_JSON = '{"regions": [{"code": "BIS"}]}'


def make(root: Path, **override: object) -> Manifest:
    (root / WORKFLOW).write_bytes(encode(TEXT))
    (root / CATALOGUE).write_bytes(encode(CATALOGUE_JSON))
    fields: dict[str, object] = {
        "service_id": "s-1",
        "workflow": "demo",
        "version": 4,
        "text_sha256": sha256_bytes(encode(TEXT)),
        "catalogue_sha256": sha256_bytes(encode(CATALOGUE_JSON)),
        "api_key_sha256": "ab" * 32,
        "created_at": "2026-11-08T10:00:00Z",
    } | override
    manifest = Manifest(**fields)  # type: ignore[arg-type]
    write_manifest(root, manifest)
    return manifest


def test_a_bundle_is_read_back_byte_for_byte(tmp_path: Path) -> None:
    manifest = make(tmp_path)
    bundle = read_bundle(tmp_path)
    assert bundle.manifest == manifest and manifest.format == FORMAT
    assert bundle.text == TEXT and bundle.catalogue == CATALOGUE_JSON


@pytest.mark.parametrize("name", [WORKFLOW, CATALOGUE])
def test_a_changed_file_is_refused(tmp_path: Path, name: str) -> None:
    make(tmp_path)
    path = tmp_path / name
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(BundleError, match=f"{name} was changed"):
        read_bundle(tmp_path)


@pytest.mark.parametrize("name", [WORKFLOW, CATALOGUE, MANIFEST])
def test_a_missing_file_is_refused(tmp_path: Path, name: str) -> None:
    make(tmp_path)
    (tmp_path / name).unlink()
    with pytest.raises(BundleError, match="has no"):
        read_bundle(tmp_path)


def test_a_manifest_that_is_not_json_or_has_other_fields_is_refused(tmp_path: Path) -> None:
    make(tmp_path)
    (tmp_path / MANIFEST).write_bytes(b"{not json")
    with pytest.raises(BundleError, match="not valid JSON"):
        read_bundle(tmp_path)
    (tmp_path / MANIFEST).write_bytes(b'{"workflow": "demo"}')
    with pytest.raises(BundleError, match="fields of format"):
        read_bundle(tmp_path)


def test_another_format_is_refused(tmp_path: Path) -> None:
    make(tmp_path, format=2)
    with pytest.raises(BundleError, match="format 2"):
        read_bundle(tmp_path)
