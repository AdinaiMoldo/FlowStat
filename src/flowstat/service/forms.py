"""The regional forms that the actions read (US-49): receive, list and remove one form per region
and quarter.

A form is one file, ``<data dir>/<region code>/<quarter>.xlsx``, which is where ``xlsx.read`` looks
(``examples/quarterly_consolidation.yaml``). The regions are the list ``regions`` of the catalogue,
so a path is never made from what a caller typed: the code is one of the codes the administrator
loaded, and the quarter has the shape ``2025Q1``. A form is accepted when it is a readable xlsx
with the header ``code, name, value``; that it also keeps the statistical rules is told to the
sender (``problems``) but does not refuse it, because the check of the rules is a step of the
workflow and what a run does with a bad form is the point of showing it.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from flowstat.actions.base import ActionError
from flowstat.actions.xlsx import read_sheet
from flowstat.model import rules as rulebook
from flowstat.model.form_versions import Comparison, FormVersion, compare
from flowstat.model.rules import Finding
from flowstat.repo.catalogue import CatalogueRepo
from flowstat.repo.form_versions import FormVersionNotFoundError, FormVersionRepo

MAX_BYTES = 2_000_000
QUARTER = re.compile(r"^20[0-9]{2}Q[1-4]$")
SAFE_CODE = re.compile(r"^[A-Za-z0-9_-]{1,16}$")
SHOWN = 5  # the rule violations that are told, as in the check of the workflow


class FormNotFound(Exception):
    """The region is not in the catalogue, or there is no form to remove."""


class FormRefused(Exception):
    """The quarter, the size or the file is not acceptable; the message says why."""


class FormLocked(Exception):
    """The campaign of the quarter has an approved result: its forms cannot change (US-53)."""


@dataclass(frozen=True)
class FormInfo:
    region: str
    name: str
    quarter: str
    received: bool
    rows: int | None = None
    size: int | None = None
    problems: tuple[str, ...] = ()  # what blocks the form: the errors, cut to the first few
    findings: tuple[Finding, ...] = ()  # everything the rules found, with where and why
    version: int | None = None  # the version that the file on the disk is (US-55)
    versions: int = 0  # how many versions of this form were ever kept
    uploaded_by: str | None = None
    uploaded_at: str | None = None


class FormService:
    def __init__(
        self,
        data_dir: Path,
        catalogue: CatalogueRepo,
        locking: Callable[[str], str | None] | None = None,
        rules_of: Callable[[str], str] | None = None,
        versions: FormVersionRepo | None = None,
        clock: Callable[[], str] | None = None,
        after_version: Callable[[str, str, int, tuple[Finding, ...], str], None] | None = None,
        after_remove: Callable[[str, str], None] | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.catalogue = catalogue
        # a function that names the campaign that locks a quarter, or None (the forms are free)
        self.locking = locking
        # a function that names the set of content rules for a quarter (the one of its campaign)
        self.rules_of = rules_of
        # where the versions are kept, and the clock that dates them; without them a form is one
        # file and nothing more, as it was before US-55
        self.versions = versions
        self.clock = clock
        # told when a new version was kept: region, quarter, its number, its findings and the
        # sender (the problems of the form are brought up to date there, US-56)
        self.after_version = after_version
        # told when the current form of a region was removed: region, quarter (US-57)
        self.after_remove = after_remove

    def _unlocked(self, quarter: str) -> None:
        held = None if self.locking is None else self.locking(quarter)
        if held is not None:
            raise FormLocked(f"the campaign {held} has an approved result for {quarter}")

    # -- what the catalogue allows ------------------------------------------------------------
    def regions(self) -> dict[str, str]:
        """Code to name, for the regions of the catalogue whose code is safe to use as a folder."""
        items = self.catalogue.all_lists().get("regions", [])
        return {
            item["code"]: item.get("name", item["code"])
            for item in items
            if SAFE_CODE.match(item.get("code", ""))
        }

    def _known(self, region: str) -> str:
        found = self.regions()
        if region not in found:
            raise FormNotFound(f"the region {region} is not in the catalogue")
        return found[region]

    @staticmethod
    def _quarter(quarter: str) -> str:
        if not QUARTER.match(quarter):
            raise FormRefused(f"the quarter {quarter!r} must look like 2025Q1")
        return quarter

    def _path(self, region: str, quarter: str) -> Path:
        return self.data_dir / region / f"{quarter}.xlsx"

    # -- reading a form that is there ----------------------------------------------------------
    def _rules(self, quarter: str, rules: str | None) -> rulebook.RuleSet:
        name = rules or (self.rules_of(quarter) if self.rules_of else "") or rulebook.BASIC.name
        return rulebook.named(name) if rulebook.known(name) else rulebook.BASIC

    def _findings(
        self, data: bytes, region: str, quarter: str, rules: str | None
    ) -> tuple[int, tuple[Finding, ...], str]:
        """The rows of a form, what the rules find in it and the rule set that was used. An
        ``ActionError`` says that the file cannot be read."""
        file = f"{region}/{quarter}.xlsx"
        sheet = read_sheet(io.BytesIO(data), file)
        chosen = self._rules(quarter, rules)
        found = rulebook.check(
            sheet.rows,
            chosen,
            region=region,
            file=file,
            sheet=sheet.title,
            positions=sheet.positions,
            sheets=sheet.sheets,
            extra_columns=sheet.extra_columns,
        )
        return len(sheet.rows), tuple(found), chosen.name

    def _describe(self, region: str, name: str, quarter: str, rules: str | None = None) -> FormInfo:
        path = self._path(region, quarter)
        kept = 0 if self.versions is None else self.versions.count(region, quarter)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return FormInfo(region, name, quarter, received=False, versions=kept)
        size = len(data)
        try:
            rows, found, _ = self._findings(data, region, quarter, rules)
        except ActionError as error:
            return FormInfo(
                region, name, quarter, True, None, size, (error.message,), versions=kept
            )
        errors = [f.message for f in rulebook.blocking(found)]
        more = [f"and {len(errors) - SHOWN} more"] if len(errors) > SHOWN else []
        # the file is a version only when it is the file that was kept: one that was put there by
        # hand, or changed after it was sent, has no version
        current = None if self.versions is None else self.versions.current(region, quarter)
        mine = current if current is not None and current.sha256 == _digest(data) else None
        return FormInfo(
            region,
            name,
            quarter,
            True,
            rows,
            size,
            (*errors[:SHOWN], *more),
            found,
            None if mine is None else mine.number,
            kept,
            None if mine is None else mine.by,
            None if mine is None else mine.at,
        )

    # -- the operations ---------------------------------------------------------------------------
    def listing(self, quarter: str, rules: str | None = None) -> list[FormInfo]:
        self._quarter(quarter)
        return [self._describe(code, name, quarter, rules) for code, name in self.regions().items()]

    def save(self, region: str, quarter: str, data: bytes, by: str = "") -> FormInfo:
        name = self._known(region)
        self._quarter(quarter)
        self._unlocked(quarter)
        if len(data) > MAX_BYTES:
            raise FormRefused(f"a form may have at most {MAX_BYTES} bytes")
        try:  # read from memory: no file, so no handle that could stay open on Windows
            rows, found, used = self._findings(data, region, quarter, None)
        except ActionError as error:
            raise FormRefused(error.message) from None
        digest = _digest(data)
        current = None if self.versions is None else self.versions.current(region, quarter)
        folder = self.data_dir / region
        folder.mkdir(parents=True, exist_ok=True)
        # written beside the place it is meant for and moved there at once, so a reader never sees
        # half a file; a file that cannot be read was refused above and never gets here
        part = folder / f".upload-{os.getpid()}-{threading.get_ident()}.xlsx"
        try:
            part.write_bytes(data)
            os.replace(part, self._path(region, quarter))
        finally:
            part.unlink(missing_ok=True)
        # a file that is sent again unchanged is the version it was, not a new one
        new = current is None or current.sha256 != digest
        if self.versions is not None and self.clock is not None and new:
            kept = self.versions.add(
                region, quarter, digest, len(data), rows, by, self.clock(), used, found, data
            )
            if self.after_version is not None:
                self.after_version(region, quarter, kept.number, found, by)
        return self._describe(region, name, quarter)

    def remove(self, region: str, quarter: str, by: str = "") -> None:
        self._known(region)
        self._quarter(quarter)
        self._unlocked(quarter)
        path = self._path(region, quarter)
        if not path.is_file():
            raise FormNotFound(f"there is no form of {region} for {quarter}")
        path.unlink()
        if self.versions is not None and self.clock is not None:
            self.versions.mark_removed(region, quarter, self.clock(), by)
        if self.after_remove is not None:
            self.after_remove(region, quarter)

    # -- the history (US-55) -----------------------------------------------------------------------
    def history(self, region: str, quarter: str) -> list[FormVersion]:
        """Every version that was kept for the region and quarter, the newest first."""
        self._known(region)
        self._quarter(quarter)
        return [] if self.versions is None else self.versions.all(region, quarter)

    def version(self, region: str, quarter: str, number: int) -> FormVersion:
        self._known(region)
        self._quarter(quarter)
        try:
            if self.versions is None:
                raise FormVersionNotFoundError(str(number))
            return self.versions.get(region, quarter, number)
        except FormVersionNotFoundError:
            raise FormNotFound(f"{region} has no version {number} for {quarter}") from None

    def compare(
        self, region: str, quarter: str, older: int | None, newer: int | None
    ) -> tuple[FormVersion, FormVersion, Comparison]:
        """What the newer version fixed, kept and brought. Without numbers: the latest version
        against the one before it."""
        kept = self.history(region, quarter)
        if newer is None:
            if not kept:
                raise FormNotFound(f"{region} has sent no form for {quarter}")
            newer = kept[0].number
        if older is None:
            older = newer - 1
            if older < 1:
                raise FormNotFound(f"version {newer} of {region} has no earlier version")
        first, second = self.version(region, quarter, older), self.version(region, quarter, newer)
        return first, second, compare(first.findings, second.findings)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
