"""US-57: a preliminary or a final result of a campaign, made from the valid forms (pure)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from flowstat.model.consolidation import (
    FINAL,
    PRELIMINARY,
    ConsolidationError,
    Left,
    Part,
    consolidate,
)

REGIONS = ("BIS", "OSC", "BAT", "JAL")


def part(region: str, version: int = 1, **values: str) -> Part:
    rows = tuple((code, f"name of {code}", Decimal(v)) for code, v in values.items())
    return Part(region, version, f"sha-{region}-{version}", rows)


def three() -> dict[str, Part]:
    return {
        "BIS": part("BIS", 2, I01="10.1", I02="3"),
        "OSC": part("OSC", 1, I01="0.2", I02="4"),
        "BAT": part("BAT", 1, I01="0.1"),
    }


LEFT = {"JAL": Left("JAL", "missing", "no form was sent")}


def test_a_preliminary_result_lists_who_is_in_and_who_is_out_and_is_marked_incomplete() -> None:
    result = consolidate(PRELIMINARY, REGIONS, three(), LEFT, preliminary_min=3, final_min=4)
    assert result.kind == PRELIMINARY and result.complete is False
    assert result.included == (
        ("BIS", 2, "sha-BIS-2"),
        ("OSC", 1, "sha-OSC-1"),
        ("BAT", 1, "sha-BAT-1"),
    )
    assert result.left_out == (Left("JAL", "missing", "no form was sent"),)
    assert (result.count, result.expected, result.share) == (3, 4, "75.0")


def test_the_sums_are_exact_and_each_total_names_the_forms_it_comes_from() -> None:
    result = consolidate(PRELIMINARY, REGIONS, three(), LEFT, preliminary_min=3, final_min=4)
    first, second = result.lines
    assert (first.code, first.total) == ("I01", Decimal("10.4"))  # 10.1 + 0.2 + 0.1, not 10.399...
    assert first.parts == (
        ("BIS", 2, Decimal("10.1")), ("OSC", 1, Decimal("0.2")), ("BAT", 1, Decimal("0.1")),
    )  # fmt: skip
    assert (second.code, second.total) == ("I02", Decimal("7"))
    assert [p[0] for p in second.parts] == ["BIS", "OSC"]  # BAT has no such row and is not counted
    assert sum((p[2] for p in first.parts), Decimal(0)) == first.total


def test_too_few_valid_forms_refuse_a_preliminary_result() -> None:
    with pytest.raises(ConsolidationError) as refused:
        consolidate(PRELIMINARY, REGIONS, three(), LEFT, preliminary_min=4, final_min=4)
    assert refused.value.code == "E-CONS-001" and "3" in str(refused.value)


def test_a_final_result_needs_the_policy_of_the_campaign() -> None:
    with pytest.raises(ConsolidationError) as refused:
        consolidate(FINAL, REGIONS, three(), LEFT, preliminary_min=3, final_min=4)
    assert refused.value.code == "E-CONS-002" and "JAL" in str(refused.value)


def test_a_final_result_with_every_form_is_complete() -> None:
    parts = {**three(), "JAL": part("JAL", 3, I01="1")}
    result = consolidate(FINAL, REGIONS, parts, {}, preliminary_min=3, final_min=4)
    assert result.kind == FINAL and result.complete is True and result.left_out == ()
    assert result.share == "100.0" and result.lines[0].total == Decimal("11.4")


def test_a_final_result_that_the_policy_allows_with_a_region_missing_is_still_incomplete() -> None:
    result = consolidate(FINAL, REGIONS, three(), LEFT, preliminary_min=2, final_min=3)
    assert result.kind == FINAL and result.complete is False


def test_the_order_is_the_order_of_the_campaign_whatever_the_order_of_the_arguments() -> None:
    parts = dict(reversed(list(three().items())))
    result = consolidate(PRELIMINARY, REGIONS, parts, LEFT, preliminary_min=1, final_min=4)
    assert [r for r, _, _ in result.included] == ["BIS", "OSC", "BAT"]
    assert [code for code in (line.code for line in result.lines)] == ["I01", "I02"]


def test_a_region_must_be_in_or_out_and_not_both_or_neither_and_the_kind_must_be_known() -> None:
    with pytest.raises(ValueError, match="JAL"):
        consolidate(PRELIMINARY, REGIONS, three(), {}, preliminary_min=1, final_min=4)
    with pytest.raises(ValueError, match="BIS"):
        both = {**LEFT, "BIS": Left("BIS", "invalid", "2 errors")}
        consolidate(PRELIMINARY, REGIONS, three(), both, preliminary_min=1, final_min=4)
    with pytest.raises(ValueError, match="kind"):
        consolidate("DRAFT", REGIONS, three(), LEFT, preliminary_min=1, final_min=4)


def test_a_code_that_comes_twice_in_one_form_is_added_and_shown_once_for_the_region() -> None:
    parts = {"BIS": Part("BIS", 1, "s", (("I01", "a", Decimal("1.5")), ("I01", "a", Decimal("2"))))}
    result = consolidate(PRELIMINARY, ("BIS",), parts, {}, preliminary_min=1, final_min=1)
    assert result.lines[0].parts == (("BIS", 1, Decimal("3.5")),)
