"""Tests of the catalogue storage (A4)."""

from __future__ import annotations

import pytest

from flowstat.repo.catalogue import CatalogueRepo, InvalidCatalogueError
from flowstat.repo.db import open_database

REGIONS = [
    {"code": "BIS", "name": "Bishkek"},
    {"code": "OSC", "name": "Osh city"},
    {"code": "BAT", "name": "Batken"},
]


def repo() -> CatalogueRepo:
    return CatalogueRepo(open_database(":memory:"))


def test_empty_catalogue() -> None:
    assert repo().all_lists() == {}
    assert repo().get_list("regions") is None


def test_set_and_get_a_list_keeps_order_and_attributes() -> None:
    r = repo()
    r.set_list("regions", REGIONS)
    assert r.get_list("regions") == REGIONS
    assert r.all_lists() == {"regions": REGIONS}


def test_setting_a_list_again_replaces_it() -> None:
    r = repo()
    r.set_list("regions", REGIONS)
    r.set_list("regions", REGIONS[:1])
    assert r.get_list("regions") == REGIONS[:1]


def test_lists_are_independent() -> None:
    r = repo()
    r.set_list("regions", REGIONS)
    r.set_list("forms", [{"code": "F1"}])
    assert sorted(r.all_lists()) == ["forms", "regions"]


def test_items_need_a_unique_code() -> None:
    r = repo()
    with pytest.raises(InvalidCatalogueError, match="code"):
        r.set_list("regions", [{"name": "no code"}])
    with pytest.raises(InvalidCatalogueError, match="duplicate"):
        r.set_list("regions", [{"code": "A"}, {"code": "A"}])
    assert r.get_list("regions") is None  # nothing was stored


def test_a_failed_replacement_keeps_the_old_list() -> None:
    r = repo()
    r.set_list("regions", REGIONS)
    with pytest.raises(InvalidCatalogueError):
        r.set_list("regions", [{"code": "A"}, {"code": "A"}])
    assert r.get_list("regions") == REGIONS


def test_the_returned_lists_are_copies() -> None:
    r = repo()
    r.set_list("regions", REGIONS)
    r.all_lists()["regions"].clear()
    assert r.get_list("regions") == REGIONS
