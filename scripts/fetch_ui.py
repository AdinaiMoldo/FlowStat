"""Fetch the editor and the graph library for the page served at /ui/.

Usage: python scripts/fetch_ui.py [--force] [--dest DIR]

The page needs Monaco (the editor), Cytoscape and dagre (the graph). They are not committed
(Monaco alone is about 100 MB unpacked), and the page does not load them from a CDN, so that it
works offline and always with the versions below. This script downloads the pinned packages from
the npm registry, checks the SHA-512 of every archive against the value written here, and unpacks
only the files the page uses into src/flowstat/api/ui/vendor/ (ignored by git). It uses the
standard library only.

Without these files the page still opens: it edits in a plain text area and lists the diagnostics.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import sys
import tarfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REGISTRY = "https://registry.npmjs.org"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = ROOT / "src" / "flowstat" / "api" / "ui" / "vendor"


@dataclass(frozen=True)
class Package:
    name: str
    version: str
    integrity: str  # "sha512-" + base64, as npm writes it
    take: tuple[tuple[str, str], ...]  # (path in the archive, path under the destination)
    skip: tuple[str, ...] = ()  # parts of a path that leave a file out of a folder that is taken
    alias: tuple[tuple[str, str], ...] = ()  # (glob under the destination, stable name beside it)


PACKAGES = (
    Package(
        "monaco-editor",
        "0.57.0",
        "sha512-5BkI9KGoqrNvBGUe15/QlZq3OooZ8WLg1AxTpaqHRCP3HNpzPPZKE2EDz8M7c+VRmCeUw1Brp4cx/PWm3kI/5A==",
        (("package/min/vs/", "monaco/vs/"), ("package/LICENSE", "licenses/monaco-editor.txt")),
        # the language servers (css, html, json, typescript) and the translations are not used
        skip=("/language/", "/nls/"),
        # the worker of the editor has a hash in its name; the page loads it by the stable name
        alias=(("monaco/vs/assets/editor.worker-*.js", "editor.worker.js"),),
    ),
    Package(
        "cytoscape",
        "3.34.4",
        "sha512-msY99tMY8iWTg+sIPGfSqDcPNOt4xcj+v/K/7qUFaCUxxlqnMkUYLBjRUzn9W5CU/R2hrmtJa5+UtovrEM2ctg==",
        (
            ("package/dist/cytoscape.min.js", "cytoscape.min.js"),
            ("package/LICENSE", "licenses/cytoscape.txt"),
        ),
    ),
    Package(
        "dagre",
        "0.8.5",
        "sha512-/aTqmnRta7x7MCCpExk7HQL2O4owCT2h8NT//9I1OQ9vt29Pa0BzSAkR5lwFUcQ7491yVi/3CXU9jQ5o0Mn2Sw==",
        (
            ("package/dist/dagre.min.js", "dagre.min.js"),
            ("package/LICENSE", "licenses/dagre.txt"),
        ),
    ),
    Package(
        "cytoscape-dagre",
        "4.0.1",
        "sha512-hyNdh8Vp1nwzBcjHnM4c71G81bFhPmQYLXD2HM9AZIvl+8TrMzWEOPW45hF/XOBQiEguPcDQzi6VrXR1XBcXOA==",
        (
            ("package/dist/cytoscape-dagre.min.js", "cytoscape-dagre.min.js"),
            ("package/LICENSE", "licenses/cytoscape-dagre.txt"),
        ),
    ),
)

Download = Callable[[str], bytes]


class FetchError(RuntimeError):
    pass


def url_of(package: Package) -> str:
    base = package.name.rsplit("/", 1)[-1]
    return f"{REGISTRY}/{package.name}/-/{base}-{package.version}.tgz"


def download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        data: bytes = response.read()
    return data


def check(package: Package, data: bytes) -> None:
    """Refuse an archive whose SHA-512 is not the one written for this version."""
    digest = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")
    if digest != package.integrity:
        raise FetchError(f"{package.name}@{package.version}: the archive does not match its hash")


def unpack(package: Package, data: bytes, dest: Path) -> int:
    """Write the wanted files under ``dest``; returns how many. Nothing is written outside it."""
    written = 0
    root = dest.resolve()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            for source, target in package.take:
                if source.endswith("/"):
                    if not member.name.startswith(source):
                        continue
                    name = target + member.name[len(source) :]
                elif member.name == source:
                    name = target
                else:
                    continue
                if any(part in "/" + name for part in package.skip):
                    continue
                path = (dest / name).resolve()
                if root not in path.parents:
                    raise FetchError(f"{member.name}: leaves the destination")
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(extracted.read())
                written += 1
    if written == 0:
        raise FetchError(f"{package.name}@{package.version}: none of the expected files was found")
    for pattern, stable in package.alias:
        found = sorted(dest.glob(pattern))
        if len(found) != 1:
            raise FetchError(f"{package.name}: {pattern} matches {len(found)} files, not one")
        (found[0].parent / stable).write_bytes(found[0].read_bytes())
    return written


def current(dest: Path) -> dict[str, str]:
    try:
        loaded = json.loads((dest / "VERSIONS.json").read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def fetch(dest: Path, force: bool = False, get: Download = download) -> list[str]:
    """Bring ``dest`` to the pinned versions; returns the lines to print."""
    wanted = {p.name: p.version for p in PACKAGES}
    if not force and current(dest) == wanted:
        return [f"already up to date in {dest}"]
    dest.mkdir(parents=True, exist_ok=True)
    lines = []
    for package in PACKAGES:
        data = get(url_of(package))
        check(package, data)
        count = unpack(package, data, dest)
        lines.append(f"{package.name} {package.version}: {count} files")
    (dest / "VERSIONS.json").write_text(json.dumps(wanted, sort_keys=True), encoding="utf8")
    return lines


def main(argv: list[str] | None = None, get: Download = download) -> int:
    parser = argparse.ArgumentParser(description="Fetch the editor and the graph library")
    parser.add_argument("--force", action="store_true", help="fetch again even if up to date")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    args = parser.parse_args(argv)
    try:
        for line in fetch(args.dest, args.force, get):
            print(line)
    except (FetchError, OSError) as error:
        print(f"could not fetch the editor: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
