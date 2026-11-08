"""The bundle of a deployed version: a manifest and the frozen files it names (api-spec section 7).

The packager (``deploy/package.py``) writes a bundle; the service reads it with ``read_bundle``,
which refuses a bundle in which a file differs from its hash. The text and the catalogue snapshot
are the whole frozen copy: the compiler is deterministic, so they give the plan the core ran.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

FORMAT = 1
MANIFEST = "manifest.json"
WORKFLOW = "workflow.yaml"
CATALOGUE = "catalogue.json"


class BundleError(ValueError):
    """The bundle is missing, malformed or changed after it was made."""


@dataclass(frozen=True, slots=True)
class Manifest:
    service_id: str
    workflow: str
    version: int
    text_sha256: str
    catalogue_sha256: str
    api_key_sha256: str
    created_at: str
    format: int = FORMAT


@dataclass(frozen=True, slots=True)
class Bundle:
    manifest: Manifest
    text: str
    catalogue: str  # JSON, the snapshot the version was compiled against
    root: Path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode(text: str) -> bytes:
    """The bytes of a frozen file: UTF-8, no translation of line ends on any system."""
    return text.encode("utf8")


def write_manifest(root: Path, manifest: Manifest) -> None:
    text = json.dumps(asdict(manifest), indent=2, sort_keys=True) + "\n"
    (root / MANIFEST).write_bytes(encode(text))


def read_bundle(root: Path) -> Bundle:
    """Read a bundle and check every hash. Raises ``BundleError`` with what is wrong."""
    try:
        raw = json.loads((root / MANIFEST).read_bytes())
    except FileNotFoundError:
        raise BundleError(f"{root} has no {MANIFEST}") from None
    except ValueError:
        raise BundleError(f"{MANIFEST} is not valid JSON") from None
    try:
        manifest = Manifest(**raw)
    except TypeError:
        raise BundleError(f"{MANIFEST} does not have the fields of format {FORMAT}") from None
    if manifest.format != FORMAT:
        raise BundleError(f"the bundle has format {manifest.format}, this service reads {FORMAT}")
    files = {}
    for name, expected in (
        (WORKFLOW, manifest.text_sha256),
        (CATALOGUE, manifest.catalogue_sha256),
    ):
        try:
            data = (root / name).read_bytes()
        except FileNotFoundError:
            raise BundleError(f"{root} has no {name}") from None
        if sha256_bytes(data) != expected:
            raise BundleError(f"{name} was changed after the bundle was made")
        files[name] = data.decode("utf8")
    return Bundle(manifest, files[WORKFLOW], files[CATALOGUE], root)
