"""Tests of the packager: the files of a bundle, their hashes, the Dockerfile, the code copy."""

from __future__ import annotations

from pathlib import Path

import pytest

from flowstat.deploy.package import REQUIREMENTS, build_bundle, render_dockerfile
from flowstat.model.versions import Version, VersionStatus
from flowstat.service.bundle import encode, read_bundle, sha256_bytes

T = "2026-11-08T10:00:00Z"
TEXT = "workflow: demo\r\nversion: 4\n# a carriage return and a non-ASCII letter: ы\n"
VERSION = Version("demo", 4, VersionStatus.PUBLISHED, TEXT, '{"regions": []}', T, T)


def test_a_process_bundle_has_the_frozen_files_the_manifest_and_nothing_for_a_container(
    tmp_path: Path,
) -> None:
    bundle = build_bundle(tmp_path, "s-1", VERSION, "ab" * 32, T, "process")
    assert bundle == tmp_path / "s-1"
    names = sorted(p.name for p in bundle.iterdir())
    assert names == ["catalogue.json", "data", "manifest.json", "workflow.yaml"]
    assert (bundle / "workflow.yaml").read_bytes() == encode(TEXT)  # byte for byte
    read = read_bundle(bundle)
    assert read.manifest.text_sha256 == sha256_bytes(encode(TEXT))
    assert read.manifest.api_key_sha256 == "ab" * 32 and read.manifest.version == 4
    assert read.text == TEXT and read.catalogue == '{"regions": []}'


def test_a_container_bundle_adds_the_dockerfile_and_a_copy_of_the_package(tmp_path: Path) -> None:
    bundle = build_bundle(tmp_path, "s-2", VERSION, "ab" * 32, T, "docker")
    dockerfile = (bundle / "Dockerfile").read_text(encoding="utf8")
    assert dockerfile.startswith("# The service of demo v4")
    assert "FROM python:3." in dockerfile and "-slim" in dockerfile
    for name in REQUIREMENTS:
        assert f"{name}==" in dockerfile  # pinned
    assert '"flowstat.api.service", "--bundle", "/service/bundle"' in dockerfile
    assert (bundle / "app" / "flowstat" / "api" / "service.py").is_file()
    assert (bundle / "app" / "flowstat" / "service" / "bundle.py").is_file()
    assert not list((bundle / "app").rglob("__pycache__"))
    assert not list((bundle / "app").rglob("*.pyc"))


def test_the_dockerfile_is_the_same_each_time_and_a_missing_value_is_an_error() -> None:
    first = render_dockerfile("demo", 4, "3.12", ["a==1", "b==2"])
    assert first == render_dockerfile("demo", 4, "3.12", ["a==1", "b==2"])
    assert "pip install --no-cache-dir a==1 b==2\n" in first
    import jinja2

    environment = jinja2.Environment(undefined=jinja2.StrictUndefined)
    with pytest.raises(jinja2.UndefinedError):
        environment.from_string("{{ nothing }}").render()


def test_a_bundle_is_never_made_over_an_existing_one(tmp_path: Path) -> None:
    build_bundle(tmp_path, "s-1", VERSION, "ab" * 32, T, "process")
    with pytest.raises(FileExistsError):
        build_bundle(tmp_path, "s-1", VERSION, "ab" * 32, T, "process")
