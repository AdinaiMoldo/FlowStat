"""NFR-01: the compiler stays interactive. Thresholds are the ones of the requirement."""

from __future__ import annotations

import time

from benchmarks.bench_compile import chain

from flowstat.compiler.catalogue import Catalogue
from flowstat.compiler.compile import compile_workflow


def _seconds(text: str) -> float:
    start = time.perf_counter()
    result = compile_workflow(text, Catalogue({}))
    elapsed = time.perf_counter() - start
    assert result.diagnostics == []
    return elapsed


def test_200_steps_compile_in_at_most_one_second() -> None:
    assert _seconds(chain(200)) <= 1.0


def test_a_5000_line_file_compiles_in_at_most_three_seconds() -> None:
    text = chain(1250)
    assert text.count("\n") >= 5000
    assert _seconds(text) <= 3.0
