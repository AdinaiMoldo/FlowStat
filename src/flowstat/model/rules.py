"""The content rules of a regional form (US-54).

A form is rows of ``code, name, value``. What it must keep is a *rule set*: the basic rules that
hold for every form (a code is given once, a value is a number and not below zero), and the rules
of a template, which know the indicators (every one is there, in its range, no part is more than
its whole, a total is the sum of its parts).

Every finding says what is wrong and where: a stable code, a severity, the region, the file, the
sheet, the cell, the actual value and what was expected. Only an ``ERROR`` blocks a form; a
``WARNING`` and an ``INFO`` are told to the sender and change nothing.

The arithmetic is exact. A cell that holds ``0.1`` is the number 0.1 (``Decimal(str(value))``, the
shortest text that gives the same float back), so a total of 0.6 for parts of 0.1, 0.2 and 0.3
holds, and one of 0.61 does not, by exactly 0.01.

Pure: no storage, no clock. The module uses the standard library only.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import Enum

from flowstat.model.diagnostics import Severity

# A sum of floats that was typed has at most 17 digits, but the exponent of a float can be 300:
# 700 digits hold every such sum without rounding.
_EXACT = 700
_NOT_APPLICABLE = "n/a"


class State(Enum):
    """What a cell of value says, so that a missing figure is never mistaken for a zero."""

    EMPTY = "empty"  # the row is there, the cell is not filled
    NOT_APPLICABLE = "not applicable"  # the region wrote n/a: nothing to report
    INVALID = "invalid"  # text, a boolean, or a number that is not finite
    ZERO = "zero"  # a figure, and it is zero
    VALUE = "value"  # any other figure (a negative one is still a figure; a rule judges it)


def read_value(value: object) -> tuple[State, Decimal | None]:
    """The state of a cell and, when it is a figure, the figure as an exact decimal."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return State.EMPTY, None
    if isinstance(value, str):
        if value.strip().lower() == _NOT_APPLICABLE:
            return State.NOT_APPLICABLE, None
        return State.INVALID, None
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        return State.INVALID, None
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    if not number.is_finite():
        return State.INVALID, None
    return (State.ZERO if number == 0 else State.VALUE), number


def plain(number: Decimal) -> str:
    """A decimal as people write it: ``100``, ``40.5``, never ``1E+2``."""
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


@dataclass(frozen=True, slots=True)
class Finding:
    code: str  # VAL-<group>-<number>, stable
    severity: Severity
    message: str  # one sentence a person can read, starting with the cell
    region: str = ""
    file: str = ""
    sheet: str = ""
    row: int | None = None  # the row of the sheet (the header is row 1)
    cell: str = ""  # ``C5``; empty when the finding is about something that is not there
    indicator: str = ""
    actual: str = ""
    expected: str = ""


@dataclass(frozen=True, slots=True)
class Indicator:
    code: str
    name: str
    required: bool = True
    may_be_na: bool = False  # the region may write n/a
    low: Decimal | None = None
    low_strict: bool = False  # the value must be more than ``low``, not just at least
    high: Decimal | None = None


@dataclass(frozen=True, slots=True)
class AtMost:
    """The figure of ``part`` is at most the figure of ``whole``."""

    part: str
    whole: str


@dataclass(frozen=True, slots=True)
class SumOf:
    """The figure of ``total`` is exactly the sum of the figures of ``parts``."""

    total: str
    parts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RuleSet:
    name: str
    indicators: tuple[Indicator, ...] = ()
    at_most: tuple[AtMost, ...] = ()
    sums: tuple[SumOf, ...] = ()
    closed: bool = False  # a code that the template does not know is told

    def indicator(self, code: str) -> Indicator | None:
        return next((i for i in self.indicators if i.code == code), None)


BASIC = RuleSet("basic")

# The five indicators of the sample forms (scripts/make_forms.py). The real template of the
# statistics committee is not known; this one stands for it, and a template with totals would list
# ``SumOf`` rules in the same way.
REGIONAL_V1 = RuleSet(
    "regional-indicators-v1",
    indicators=(
        Indicator("I01", "Population", low=Decimal(0), low_strict=True),
        Indicator("I02", "Households"),
        Indicator("I03", "Births"),
        Indicator("I04", "Deaths"),
        Indicator("I05", "Marriages"),
    ),
    at_most=(
        AtMost("I02", "I01"),
        AtMost("I03", "I01"),
        AtMost("I04", "I01"),
        AtMost("I05", "I01"),
    ),
    closed=True,
)

SETS = {s.name: s for s in (BASIC, REGIONAL_V1)}


def known(name: str) -> bool:
    return name in SETS


def named(name: str) -> RuleSet:
    return SETS[name]


def blocking(found: Sequence[Finding]) -> list[Finding]:
    """The findings that stop a form: the errors."""
    return [f for f in found if f.severity is Severity.ERROR]


def check(
    rows: Sequence[object],
    rules: RuleSet = BASIC,
    *,
    region: str = "",
    file: str = "",
    sheet: str = "",
    positions: Sequence[int] | None = None,
    sheets: int = 1,
    extra_columns: bool = False,
) -> list[Finding]:
    """What the rows of a form break under ``rules``, in the order of the rows, then what is
    missing, then the relations between indicators.

    ``positions`` are the rows of the sheet that the rows came from (when empty lines were skipped
    they are not 2, 3, 4 ...); ``sheets`` and ``extra_columns`` are what the reader saw of the file.
    """
    found: list[Finding] = []

    def add(
        code: str,
        severity: Severity,
        message: str,
        row: int | None = None,
        column: str = "C",
        indicator: str = "",
        actual: str = "",
        expected: str = "",
    ) -> None:
        cell = f"{column}{row}" if row is not None else ""
        found.append(
            Finding(
                code, severity, message, region, file, sheet, row, cell, indicator, actual, expected
            )
        )

    if sheets > 1:
        add(
            "VAL-STR-006",
            Severity.WARNING,
            f"the file has {sheets} sheets: only the first is read",
            actual=str(sheets),
            expected="1 sheet",
        )
    if extra_columns:
        add(
            "VAL-STR-007",
            Severity.WARNING,
            "there are cells right of the column value: they are not read",
            expected="3 columns: code, name, value",
        )
    if not rows:
        add("VAL-STR-001", Severity.ERROR, "the form has no rows", expected="at least one row")
        return found

    seen: set[str] = set()
    figures: dict[str, tuple[Decimal, int]] = {}  # the figures that no rule has refused
    for index, record in enumerate(rows):
        number = positions[index] if positions is not None else index + 2
        if not isinstance(record, dict):
            add(
                "VAL-STR-002",
                Severity.ERROR,
                f"row {number}: not a record",
                number,
                "A",
                expected="code, name, value",
            )
            continue
        raw = record.get("code")
        code = raw.strip() if isinstance(raw, str) else ""
        if not code:
            add(
                "VAL-STR-003",
                Severity.ERROR,
                f"A{number}: code is empty",
                number,
                "A",
                actual=repr(raw) if raw is not None else "",
                expected="a code",
            )
        elif code in seen:
            add(
                "VAL-STR-004",
                Severity.ERROR,
                f"A{number}: duplicate code {code}",
                number,
                "A",
                code,
                actual=code,
                expected="each code once",
            )
        first = bool(code) and code not in seen
        if code:
            seen.add(code)
        indicator = rules.indicator(code)
        tag = f" ({code})" if code else ""
        if indicator is None and rules.closed and first:
            add(
                "VAL-STR-008",
                Severity.WARNING,
                f"A{number}: the code {code} is not in the template {rules.name}",
                number,
                "A",
                code,
                actual=code,
                expected="a code of the template",
            )
        name = record.get("name")
        if (
            indicator is not None
            and isinstance(name, str)
            and name.strip()
            and name.strip().casefold() != indicator.name.casefold()
        ):
            add(
                "VAL-STR-005",
                Severity.WARNING,
                f"B{number}{tag}: the name differs from the template",
                number,
                "B",
                code,
                actual=name.strip(),
                expected=indicator.name,
            )

        state, value = read_value(record.get("value"))
        cell = f"C{number}{tag}"
        shown = "" if record.get("value") is None else str(record.get("value"))
        before = len(found)
        if state is State.EMPTY:
            add(
                "VAL-COMP-002",
                Severity.ERROR,
                f"{cell}: value is not a number: the cell is empty",
                number,
                indicator=code,
                expected="a number (0 when there is none, n/a when it does not apply)",
            )
        elif state is State.INVALID:
            add(
                "VAL-TYPE-001",
                Severity.ERROR,
                f"{cell}: value is not a number: {shown!r}",
                number,
                indicator=code,
                actual=shown,
                expected="a number",
            )
        elif state is State.NOT_APPLICABLE:
            if indicator is not None and indicator.may_be_na:
                add(
                    "VAL-COMP-004",
                    Severity.INFO,
                    f"{cell}: reported as not applicable",
                    number,
                    indicator=code,
                    actual="n/a",
                )
            else:
                add(
                    "VAL-COMP-004",
                    Severity.ERROR,
                    f"{cell}: value is not a number: n/a is not allowed here",
                    number,
                    indicator=code,
                    actual="n/a",
                    expected="a number",
                )
        else:
            assert value is not None
            if value < 0:
                add(
                    "VAL-RANGE-001",
                    Severity.ERROR,
                    f"{cell}: value is negative",
                    number,
                    indicator=code,
                    actual=plain(value),
                    expected="0 or more",
                )
            elif indicator is not None:
                _range(indicator, value, add, number, cell)
            if state is State.ZERO and len(found) == before and indicator is not None:
                add(
                    "VAL-COMP-003",
                    Severity.INFO,
                    f"{cell}: zero is reported (a figure, not a missing value)",
                    number,
                    indicator=code,
                    actual="0",
                )
        refused = any(f.severity is Severity.ERROR for f in found[before:])
        usable = state in (State.ZERO, State.VALUE) and value is not None and code and first
        if usable and not refused and value is not None:  # a refused figure is not used further
            figures[code] = (value, number)

    for needed in rules.indicators:
        if needed.required and needed.code not in seen:
            add(
                "VAL-COMP-001",
                Severity.ERROR,
                f"{needed.code} {needed.name} is missing",
                indicator=needed.code,
                expected=f"a row with the code {needed.code}",
            )
    for relation in rules.at_most:
        part, whole = figures.get(relation.part), figures.get(relation.whole)
        if part is not None and whole is not None and part[0] > whole[0]:
            add(
                "VAL-REL-001",
                Severity.ERROR,
                f"C{part[1]} ({relation.part}): {plain(part[0])} is more than "
                f"{plain(whole[0])} in {relation.whole}",
                part[1],
                indicator=relation.part,
                actual=plain(part[0]),
                expected=f"at most {plain(whole[0])} ({relation.whole})",
            )
    for rule in rules.sums:
        total = figures.get(rule.total)
        parts = [figures.get(p) for p in rule.parts]
        if total is None or any(p is None for p in parts):
            continue
        with localcontext() as context:
            context.prec = _EXACT
            added = sum((p[0] for p in parts if p is not None), Decimal(0))
            gap = total[0] - added
        if gap != 0:
            names = " + ".join(rule.parts)
            add(
                "VAL-ARITH-001",
                Severity.ERROR,
                f"C{total[1]} ({rule.total}): {plain(total[0])} differs by {plain(abs(gap))} "
                f"from the sum of {names}, which is {plain(added)}",
                total[1],
                indicator=rule.total,
                actual=plain(total[0]),
                expected=f"{plain(added)} ({names})",
            )
    return found


def _range(
    indicator: Indicator, value: Decimal, add: Callable[..., None], number: int, cell: str
) -> None:
    """The range of an indicator; a value that is negative was told by the basic rule already."""
    low, high = indicator.low, indicator.high
    below = low is not None and (value < low or (indicator.low_strict and value == low))
    above = high is not None and value > high
    if below:
        assert low is not None
        word = "more than" if indicator.low_strict else "at least"
        expected = f"{word} {plain(low)}"
    elif above:
        assert high is not None
        expected = f"at most {plain(high)}"
    else:
        return
    add(
        "VAL-RANGE-002",
        Severity.ERROR,
        f"{cell}: value {plain(value)} is out of range",
        number,
        indicator=indicator.code,
        actual=plain(value),
        expected=expected,
    )
