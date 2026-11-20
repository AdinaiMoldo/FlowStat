"""The result of a campaign: the national figures made from the valid forms (US-57).

A result is preliminary or final. A preliminary one can be made as soon as enough valid forms are
there (``preliminary_min`` of the campaign); a final one needs the policy of completeness of the
campaign (``final_min``) and is refused otherwise. A result says which regions are in it, with the
version of the form of each, and which are left out and why, so that nobody reads a total of seven
regions as the total of the country. It is *complete* only when every region of the campaign is in
it; that is a fact of the data and not a label of the page.

Every total keeps the figures it was added from (region, form version, value), so a number can be
traced to the forms. The sums are exact (``decimal.Decimal``): 10.1 + 0.2 + 0.1 is 10.4.

Pure: no storage, no clock. The module uses the standard library only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

PRELIMINARY = "PRELIMINARY"
FINAL = "FINAL"
KINDS = (PRELIMINARY, FINAL)

MISSING = "missing"  # no current form
INVALID = "invalid"  # the current form has errors that block it


class ConsolidationError(ValueError):
    """A result that cannot be made; ``code`` says which rule refused it."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Part:
    """The valid current form of one region: (code, name, value) for each of its figures."""

    region: str
    version: int
    sha256: str
    rows: tuple[tuple[str, str, Decimal], ...]


@dataclass(frozen=True, slots=True)
class Left:
    """A region that is not in the result, and why."""

    region: str
    reason: str  # MISSING or INVALID
    detail: str


@dataclass(frozen=True, slots=True)
class Line:
    """One figure of the result and where it comes from."""

    code: str
    name: str
    total: Decimal
    parts: tuple[tuple[str, int, Decimal], ...]  # region, form version, value


@dataclass(frozen=True, slots=True)
class Result:
    kind: str
    complete: bool  # every region of the campaign is in it
    expected: int
    included: tuple[tuple[str, int, str], ...]  # region, form version, sha256 of the file
    left_out: tuple[Left, ...]
    lines: tuple[Line, ...]

    @property
    def count(self) -> int:
        return len(self.included)

    @property
    def share(self) -> str:
        """The regions that are in, as a percentage of those expected, with one decimal."""
        return str((Decimal(self.count * 100) / Decimal(self.expected)).quantize(Decimal("0.1")))


def consolidate(
    kind: str,
    regions: Sequence[str],
    parts: Mapping[str, Part],
    left: Mapping[str, Left],
    preliminary_min: int,
    final_min: int,
) -> Result:
    """The result of ``kind`` for the regions of a campaign, in their order.

    Every region is either in ``parts`` (a valid form) or in ``left`` (with the reason). Raises
    ``ConsolidationError`` when the campaign does not have enough valid forms for the kind.
    """
    if kind not in KINDS:
        raise ValueError(f"the kind must be one of {', '.join(KINDS)}")
    for region in regions:
        if (region in parts) == (region in left):
            raise ValueError(f"the region {region} must be either included or left out")
    included = [parts[r] for r in regions if r in parts]
    out = tuple(left[r] for r in regions if r in left)
    have = len(included)
    if kind == PRELIMINARY and have < preliminary_min:
        raise ConsolidationError(
            "E-CONS-001",
            f"a preliminary result needs {preliminary_min} valid forms; {have} are valid",
        )
    if kind == FINAL and have < final_min:
        names = ", ".join(item.region for item in out)
        raise ConsolidationError(
            "E-CONS-002",
            f"the final result needs {final_min} valid forms; {have} are valid (not in: {names})",
        )
    names_of: dict[str, str] = {}
    figures: dict[str, list[tuple[str, int, Decimal]]] = {}
    for part in included:
        own: dict[str, Decimal] = {}
        for code, name, value in part.rows:
            names_of.setdefault(code, name)
            own[code] = own.get(code, Decimal(0)) + value
        for code, value in own.items():
            figures.setdefault(code, []).append((part.region, part.version, value))
    lines = tuple(
        Line(code, names_of[code], sum((v for _, _, v in figures[code]), Decimal(0)),
             tuple(figures[code]))
        for code in sorted(figures)
    )  # fmt: skip
    return Result(
        kind,
        have == len(regions),
        len(regions),
        tuple((p.region, p.version, p.sha256) for p in included),
        out,
        lines,
    )
