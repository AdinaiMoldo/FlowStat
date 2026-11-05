"""Words for the findings of the analysis: a case (values of variables) as text."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction


def describe(case: dict[str, object] | None) -> str:
    """``name = value`` for each variable of a case, in the order of the names."""
    assert case is not None
    return ", ".join(f"{name} = {value(v)}" for name, v in sorted(case.items()))


def value(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return f"'{v}'"
    if isinstance(v, Fraction):
        return decimal(v)
    return str(v)


def decimal(f: Fraction) -> str:
    """An exact decimal when there is one (1/2 is 0.5), else the fraction."""
    if f.denominator == 1:
        return str(f.numerator)
    rest = f.denominator
    for factor in (2, 5):
        while rest % factor == 0:
            rest //= factor
    if rest == 1:
        return format(Decimal(f.numerator) / Decimal(f.denominator), "f")
    return f"{f.numerator}/{f.denominator}"
