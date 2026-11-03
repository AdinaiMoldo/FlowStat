"""Compile time against workflow size (NFR-01). Run: python benchmarks/bench_compile.py

Writes a table to stdout; the conclusion is kept in docs/research/compile-time.md.
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flowstat.compiler.catalogue import Catalogue
from flowstat.compiler.compile import compile_workflow


def chain(steps: int) -> str:
    """A workflow of ``steps`` steps; step i needs step i-1 and reads its output."""
    lines = ["workflow: bench", "version: 1", "inputs:", "  q: {type: string}", "steps:"]
    for i in range(steps):
        lines += [f"  s{i}:", "    action: x.y"]
        if i:
            lines += [f"    needs: [s{i - 1}]", f'    when: "${{s{i - 1}.ok}} == true"']
        else:
            lines += ['    with: {p: "${inputs.q}"}']
    lines.append(f"goals: {{any_of: [s{steps - 1}]}}")
    return "\n".join(lines) + "\n"


def fan(width: int) -> str:
    """One foreach step over ``width`` items, a check per item, and a quorum join."""
    return (
        "workflow: bench\nversion: 1\nsteps:\n"
        "  load:\n    foreach: r in catalogue.items\n    action: x.y\n"
        "  check:\n    foreach: r in catalogue.items\n    action: x.y\n"
        '    needs: ["load[r]"]\n'
        f'  join:\n    action: x.y\n    needs: ["check[*]"]\n    join: {{at_least: {width // 2}}}\n'
        "goals: {any_of: [join]}\n"
    )


def timed(text: str, catalogue: Catalogue, repeat: int = 5) -> float:
    samples = []
    for _ in range(repeat):
        start = time.perf_counter()
        result = compile_workflow(text, catalogue)
        samples.append(time.perf_counter() - start)
        assert result.diagnostics == [], result.diagnostics[:2]
    return statistics.median(samples)


def main() -> None:
    empty = Catalogue({})
    print("| shape | steps after expansion | lines | median compile time |")
    print("| --- | --- | --- | --- |")
    for n in (50, 200, 1000, 2000):
        text = chain(n)
        print(f"| chain | {n} | {text.count(chr(10))} | {timed(text, empty) * 1000:.0f} ms |")
    for width in (9, 100, 500):
        cat = Catalogue({"items": tuple({"code": f"C{i}"} for i in range(width))})
        text = fan(width)
        lines, ms = text.count(chr(10)), timed(text, cat) * 1000
        print(f"| fan-out {width} | {2 * width + 1} | {lines} | {ms:.0f} ms |")


if __name__ == "__main__":
    main()
