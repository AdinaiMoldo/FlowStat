"""scripts/fetch_ui.py checks what it downloads and writes only where it should (no network)."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import sys
import tarfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fetch_ui", ROOT / "scripts" / "fetch_ui.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["fetch_ui"] = module
    spec.loader.exec_module(module)
    return module


fetch_ui = load()


def archive(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def integrity(data: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")


def package(data: bytes, **options: object) -> object:
    return fetch_ui.Package(
        "tool",
        "1.0.0",
        integrity(data),
        (("package/dist/tool.min.js", "tool.min.js"), ("package/LICENSE", "licenses/tool.txt")),
        **options,
    )


FILES = {"package/dist/tool.min.js": b"code", "package/LICENSE": b"MIT", "package/other": b"x"}


def test_every_pinned_package_has_a_sha512_and_a_versioned_url() -> None:
    names = {p.name for p in fetch_ui.PACKAGES}
    assert names == {"monaco-editor", "cytoscape", "dagre", "cytoscape-dagre"}
    for p in fetch_ui.PACKAGES:
        assert p.integrity.startswith("sha512-")
        assert len(base64.b64decode(p.integrity[7:])) == 64
        assert fetch_ui.url_of(p) == (
            f"https://registry.npmjs.org/{p.name}/-/{p.name}-{p.version}.tgz"
        )


def test_an_archive_that_does_not_match_its_hash_is_refused() -> None:
    data = archive(FILES)
    with pytest.raises(fetch_ui.FetchError, match="does not match"):
        fetch_ui.check(package(data), data + b"tampered")
    fetch_ui.check(package(data), data)


def test_only_the_wanted_files_are_written(tmp_path: Path) -> None:
    data = archive(FILES)
    assert fetch_ui.unpack(package(data), data, tmp_path) == 2
    assert (tmp_path / "tool.min.js").read_bytes() == b"code"
    assert (tmp_path / "licenses" / "tool.txt").read_bytes() == b"MIT"
    assert not (tmp_path / "other").exists()


def test_a_folder_is_taken_without_the_parts_that_are_skipped(tmp_path: Path) -> None:
    files = {
        "package/min/vs/editor/a.js": b"a",
        "package/min/vs/language/ts/b.js": b"b",
        "package/min/vs/nls/c.js": b"c",
    }
    data = archive(files)
    wanted = fetch_ui.Package(
        "tool",
        "1.0.0",
        integrity(data),
        (("package/min/vs/", "vs/"),),
        skip=("/language/", "/nls/"),
    )
    assert fetch_ui.unpack(wanted, data, tmp_path) == 1
    assert (tmp_path / "vs" / "editor" / "a.js").exists()
    assert not (tmp_path / "vs" / "language").exists()


def test_a_name_with_a_hash_gets_a_stable_alias(tmp_path: Path) -> None:
    data = archive({"package/min/vs/assets/worker-AB12.js": b"w"})
    wanted = fetch_ui.Package(
        "tool",
        "1.0.0",
        integrity(data),
        (("package/min/vs/", "vs/"),),
        alias=(("vs/assets/worker-*.js", "worker.js"),),
    )
    fetch_ui.unpack(wanted, data, tmp_path)
    assert (tmp_path / "vs" / "assets" / "worker.js").read_bytes() == b"w"


def test_a_path_that_climbs_out_of_the_folder_is_refused(tmp_path: Path) -> None:
    data = archive({"package/../../evil.js": b"x"})
    wanted = fetch_ui.Package("tool", "1.0.0", integrity(data), (("package/", "p/"),))
    with pytest.raises(fetch_ui.FetchError, match="leaves"):
        fetch_ui.unpack(wanted, data, tmp_path / "inside")


def test_an_archive_without_the_expected_files_is_refused(tmp_path: Path) -> None:
    data = archive({"package/else": b"x"})
    with pytest.raises(fetch_ui.FetchError, match="none of the expected"):
        fetch_ui.unpack(package(data), data, tmp_path)


def test_fetch_downloads_checks_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = archive(FILES)
    only = (package(data),)
    monkeypatch.setattr(fetch_ui, "PACKAGES", only)
    calls: list[str] = []

    def get(url: str) -> bytes:
        calls.append(url)
        return data

    first = fetch_ui.fetch(tmp_path, get=get)
    assert first == ["tool 1.0.0: 2 files"]
    assert fetch_ui.fetch(tmp_path, get=get) == [f"already up to date in {tmp_path}"]
    assert len(calls) == 1
    fetch_ui.fetch(tmp_path, force=True, get=get)
    assert len(calls) == 2


def test_main_reports_a_failure_and_returns_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    data = archive(FILES)
    monkeypatch.setattr(fetch_ui, "PACKAGES", (package(data),))
    assert fetch_ui.main(["--dest", str(tmp_path)], get=lambda url: b"not the archive") == 1
    assert "could not fetch the editor" in capsys.readouterr().err
    assert fetch_ui.main(["--dest", str(tmp_path)], get=lambda url: data) == 0
