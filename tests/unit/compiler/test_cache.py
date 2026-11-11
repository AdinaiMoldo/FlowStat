"""The cache of compiled plans (US-41): a bounded, thread-safe memo with a count of its hits."""

from __future__ import annotations

import threading

import pytest

from flowstat.compiler.cache import PlanCache


def test_a_value_is_made_once_for_a_key() -> None:
    cache: PlanCache[int] = PlanCache(4)
    made: list[str] = []

    def make(key: str) -> int:
        made.append(key)
        return len(made)

    assert [cache.get("a", lambda: make("a")) for _ in range(3)] == [1, 1, 1]
    assert cache.get("b", lambda: make("b")) == 2
    assert made == ["a", "b"] and (cache.hits, cache.misses) == (2, 2)


def test_the_least_recently_used_key_goes_first_when_the_cache_is_full() -> None:
    cache: PlanCache[str] = PlanCache(2)
    cache.get("a", lambda: "A")
    cache.get("b", lambda: "B")
    cache.get("a", lambda: "A2")  # a is the most recent now
    cache.get("c", lambda: "C")  # b goes
    assert len(cache) == 2
    assert cache.get("a", lambda: "never") == "A"
    assert cache.get("b", lambda: "B2") == "B2"  # made again


def test_an_error_is_not_kept() -> None:
    cache: PlanCache[int] = PlanCache(2)

    def broken() -> int:
        raise ValueError("no plan")

    for _ in range(2):
        with pytest.raises(ValueError):
            cache.get("a", broken)
    assert len(cache) == 0 and cache.get("a", lambda: 5) == 5


def test_a_size_of_zero_keeps_nothing_and_makes_every_time() -> None:
    cache: PlanCache[int] = PlanCache(0)
    calls: list[int] = []

    def make() -> int:
        calls.append(1)
        return len(calls)

    for _ in range(3):
        cache.get("a", make)
    assert len(calls) == 3 and len(cache) == 0


def test_a_negative_size_is_refused() -> None:
    with pytest.raises(ValueError):
        PlanCache(-1)


def test_threads_that_ask_together_all_get_a_value_and_the_cache_stays_within_its_size() -> None:
    cache: PlanCache[int] = PlanCache(8)
    gate = threading.Barrier(8)
    found: list[int] = []

    def work(n: int) -> None:
        gate.wait()
        for i in range(200):
            found.append(cache.get(f"k{(n + i) % 20}", lambda: n))

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(found) == 1600 and len(cache) <= 8
