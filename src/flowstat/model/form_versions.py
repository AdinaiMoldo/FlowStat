"""The versions of a regional form (US-55).

A region that sends its form again does not overwrite the first: each accepted file is a version,
with who sent it, when, and what the content rules found in it at that time. The current version is
the latest one that was not removed. To tell a region what its second form fixed, the findings of
two versions are compared.

A finding is *the same mistake* in two versions when it has the same code and the same indicator
(or, for a finding without an indicator, the same cell): a value that is still negative is the same
mistake although the region moved it to another row. When one mistake was there twice and is there
once, one of them is fixed.

Pure: no storage, no clock. The module uses the standard library only.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from flowstat.model.diagnostics import Severity
from flowstat.model.rules import Finding


@dataclass(frozen=True, slots=True)
class FormVersion:
    region: str
    quarter: str
    number: int  # 1, 2, 3 ... for a region and a quarter
    sha256: str  # of the file: a file that is sent again unchanged is not a new version
    size: int
    rows: int
    by: str  # the name of the user who sent it
    at: str  # the clock of the core, ISO 8601
    rules: str  # the set of content rules that was applied
    findings: tuple[Finding, ...]
    removed_at: str | None = None
    removed_by: str | None = None

    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f.severity is Severity.ERROR)

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f.severity is Severity.WARNING)


@dataclass(frozen=True, slots=True)
class Comparison:
    fixed: tuple[Finding, ...]  # in the older version, not in the newer
    remaining: tuple[Finding, ...]  # in both (as they are in the newer)
    introduced: tuple[Finding, ...]  # only in the newer


def _same(finding: Finding) -> tuple[str, str]:
    return finding.code, finding.indicator or finding.cell


def compare(older: Sequence[Finding], newer: Sequence[Finding]) -> Comparison:
    """What the newer version fixed, kept and brought, by the findings of the two."""
    unmatched = list(newer)
    fixed: list[Finding] = []
    remaining: list[Finding] = []
    for before in older:
        key = _same(before)
        at = next((i for i, after in enumerate(unmatched) if _same(after) == key), None)
        if at is None:
            fixed.append(before)
        else:
            remaining.append(unmatched.pop(at))
    return Comparison(tuple(fixed), tuple(remaining), tuple(unmatched))


def dump_findings(findings: Sequence[Finding]) -> str:
    """The findings as text, to be kept with a version."""
    return json.dumps(
        [
            [f.code, f.severity.value, f.message, f.region, f.file, f.sheet, f.row, f.cell,
             f.indicator, f.actual, f.expected]
            for f in findings
        ],
        ensure_ascii=False,
    )  # fmt: skip


def load_findings(text: str) -> tuple[Finding, ...]:
    return tuple(
        Finding(
            code, Severity(severity), message, region, file, sheet, row, cell, indicator, actual,
            expected,
        )
        for (code, severity, message, region, file, sheet, row, cell, indicator, actual, expected)
        in json.loads(text)
    )  # fmt: skip
