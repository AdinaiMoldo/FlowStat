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

import io
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from flowstat.actions.base import ActionError
from flowstat.actions.xlsx import read_sheet, rows_of
from flowstat.model import rules as content
from flowstat.model.rules import Finding
from flowstat.repo.catalogue import CatalogueRepo

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


class FormService:
    def __init__(
        self,
        data_dir: Path,
        catalogue: CatalogueRepo,
        locking: Callable[[str], str | None] | None = None,
        rules_of: Callable[[str], str] | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.catalogue = catalogue
        # a function that names the campaign that locks a quarter, or None (the forms are free)
        self.locking = locking
        # a function that names the set of content rules for a quarter (the one of its campaign)
        self.rules_of = rules_of

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
    def _rules(self, quarter: str, rules: str | None) -> content.RuleSet:
        name = rules or (self.rules_of(quarter) if self.rules_of else "") or content.BASIC.name
        return content.named(name) if content.known(name) else content.BASIC

    def _describe(self, region: str, name: str, quarter: str, rules: str | None = None) -> FormInfo:
        file = f"{region}/{quarter}.xlsx"
        path = self._path(region, quarter)
        if not path.is_file():
            return FormInfo(region, name, quarter, received=False)
        size = path.stat().st_size
        try:
            with open(path, "rb") as stream:
                sheet = read_sheet(stream, file)
        except FileNotFoundError:  # removed while we looked
            return FormInfo(region, name, quarter, received=False)
        except ActionError as error:
            return FormInfo(region, name, quarter, True, None, size, (error.message,))
        found = content.check(
            sheet.rows,
            self._rules(quarter, rules),
            region=region,
            file=file,
            sheet=sheet.title,
            positions=sheet.positions,
            sheets=sheet.sheets,
            extra_columns=sheet.extra_columns,
        )
        errors = [f.message for f in content.blocking(found)]
        more = [f"and {len(errors) - SHOWN} more"] if len(errors) > SHOWN else []
        return FormInfo(
            region,
            name,
            quarter,
            True,
            len(sheet.rows),
            size,
            (*errors[:SHOWN], *more),
            tuple(found),
        )

    # -- the operations ---------------------------------------------------------------------------
    def listing(self, quarter: str, rules: str | None = None) -> list[FormInfo]:
        self._quarter(quarter)
        return [self._describe(code, name, quarter, rules) for code, name in self.regions().items()]

    def save(self, region: str, quarter: str, content: bytes) -> FormInfo:
        name = self._known(region)
        self._quarter(quarter)
        self._unlocked(quarter)
        if len(content) > MAX_BYTES:
            raise FormRefused(f"a form may have at most {MAX_BYTES} bytes")
        try:  # read from memory: no file, so no handle that could stay open on Windows
            rows_of(io.BytesIO(content), "the file")
        except ActionError as error:
            raise FormRefused(error.message) from None
        folder = self.data_dir / region
        folder.mkdir(parents=True, exist_ok=True)
        # written beside the place it is meant for and moved there at once, so a reader never sees
        # half a file; a file that cannot be read was refused above and never gets here
        part = folder / f".upload-{os.getpid()}-{threading.get_ident()}.xlsx"
        try:
            part.write_bytes(content)
            os.replace(part, self._path(region, quarter))
        finally:
            part.unlink(missing_ok=True)
        return self._describe(region, name, quarter)

    def remove(self, region: str, quarter: str) -> None:
        self._known(region)
        self._quarter(quarter)
        self._unlocked(quarter)
        path = self._path(region, quarter)
        if not path.is_file():
            raise FormNotFound(f"there is no form of {region} for {quarter}")
        path.unlink()
