"""Make the burndown and the velocity chart of the project from docs/backlog.md (DOC-7).

Usage: python scripts/make_charts.py [--dest DIR] [--check]

The charts are not drawn by hand, so they cannot disagree with the backlog. The plan has no due
dates, so the time axis is the milestone (docs/backlog.md says why). What is read:

* the table of milestones: the size of each, as ``base`` or ``base + added`` (the added part is the
  scope that grew while the milestone ran; M9 is all added, it came from the audit of 9 November);
* the table of stories: the size and the status of each story.

A milestone that is done delivered its whole size. A milestone that is not done delivered the sum
of its stories that are done.

Writes ``burndown.svg``, ``velocity.svg`` and ``data.csv`` into docs/charts/. With ``--check``
nothing is written and the script exits with 1 if a file there is not what the backlog gives now.
It uses the standard library only; the output has no date, so the same backlog gives the same bytes.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKLOG = ROOT / "docs" / "backlog.md"
DEFAULT_DEST = ROOT / "docs" / "charts"


@dataclass(frozen=True)
class Milestone:
    key: str  # "M1"
    base: int  # planned when the project was planned
    added: int  # scope that came while it ran, or after
    delivered: int

    @property
    def size(self) -> int:
        return self.base + self.added


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse(text: str) -> list[Milestone]:
    """The milestones M1 to M9 with what each delivered."""
    stories: dict[str, list[tuple[int, str]]] = {}
    sizes: list[tuple[str, int, int, str]] = []
    for line in text.splitlines():
        if re.match(r"^\| (US|DOC)-\d+ ", line):
            c = cells(line)
            stories.setdefault(c[5], []).append((int(c[3]), c[6]))
        match = re.match(r"^\| (M\d+)\b[^|]*\| ([^|]+) \|", line)
        if match and match.group(1) != "M0":
            rest = cells(line)
            sp = rest[1]
            first, _, second = (part.strip() for part in sp.partition("+"))
            if second == "" and match.group(1) == "M9":
                first, second = "0", first  # all of M9 is scope that came after the plan
            sizes.append((match.group(1), int(first), int(second or 0), rest[4]))
    found: list[Milestone] = []
    for key, base, added, status in sizes:
        if status.startswith("done"):
            delivered = base + added
        else:
            delivered = sum(sp for sp, st in stories.get(key, []) if st.startswith("done"))
        found.append(Milestone(key, base, added, delivered))
    return found


def burndown(milestones: list[Milestone]) -> list[tuple[str, int, int]]:
    """(label, scope, remaining) at the start and after every milestone.

    The scope is what was planned and what was added up to that point, the added part of a
    milestone counted from the milestone that it was added in.
    """
    baseline = sum(m.base for m in milestones)
    points = [("start", baseline, baseline)]
    scope, done = baseline, 0
    for m in milestones:
        scope += m.added
        done += m.delivered
        points.append((m.key, scope, scope - done))
    return points


# -- drawing ------------------------------------------------------------------------------------
W, H = 760, 420
LEFT, RIGHT, TOP, BOTTOM = 64, 24, 48, 56
INK, MUTED, GRID = "#1f2933", "#617080", "#d9dee4"
BLUE, GREY, ORANGE = "#2563a8", "#8a94a0", "#c2570c"


def frame(title: str, subtitle: str, top: int, labels: list[str]) -> tuple[list[str], float, float]:
    """The axes. Returns the svg parts, the x step and the y scale (pixels per unit)."""
    plot_h = H - TOP - BOTTOM
    scale = plot_h / top
    step = (W - LEFT - RIGHT) / len(labels)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        'font-family="Helvetica, Arial, sans-serif" role="img">',
        f"<title>{title}</title>",
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
        f'<text x="{LEFT}" y="24" font-size="16" font-weight="bold" fill="{INK}">{title}</text>',
        f'<text x="{LEFT}" y="40" font-size="12" fill="{MUTED}">{subtitle}</text>',
    ]
    for tick in range(0, top + 1, 10 if top <= 60 else 50):
        y = H - BOTTOM - tick * scale
        parts.append(
            f'<line x1="{LEFT}" y1="{y:.1f}" x2="{W - RIGHT}" y2="{y:.1f}" stroke="{GRID}"/>'
        )
        parts.append(
            f'<text x="{LEFT - 8}" y="{y + 4:.1f}" font-size="11" text-anchor="end" '
            f'fill="{MUTED}">{tick}</text>'
        )
    for i, label in enumerate(labels):
        x = LEFT + step * (i + 0.5)
        parts.append(
            f'<text x="{x:.1f}" y="{H - BOTTOM + 20}" font-size="12" text-anchor="middle" '
            f'fill="{INK}">{label}</text>'
        )
    parts.append(
        f'<text x="16" y="{TOP + (H - TOP - BOTTOM) / 2:.0f}" font-size="12" fill="{MUTED}" '
        f'transform="rotate(-90 16 {TOP + (H - TOP - BOTTOM) / 2:.0f})" text-anchor="middle">'
        "story points</text>"
    )
    return parts, step, scale


def roof(value: int) -> int:
    """The top of the axis: the next multiple of 10 up to 60, of 50 above."""
    unit = 10 if value <= 60 else 50
    return (value + unit - 1) // unit * unit


def burndown_svg(milestones: list[Milestone]) -> str:
    points = burndown(milestones)
    parts, step, scale = frame(
        "Burndown by milestone",
        "Story points still to do after each milestone. The grey line is the scope: it grew "
        "while the project ran.",
        roof(max(p[1] for p in points)),
        [p[0] for p in points],
    )
    base = H - BOTTOM

    def xy(i: int, v: int) -> tuple[float, float]:
        return LEFT + step * (i + 0.5), base - v * scale

    scope = []
    for i, (_, s, _) in enumerate(points):
        if i:  # a step: the scope changes at the milestone, not between two
            scope.append(xy(i, points[i - 1][1]))
        scope.append(xy(i, s))
    parts.append(
        f'<polyline fill="none" stroke="{GREY}" stroke-width="2" stroke-dasharray="6 4" points="'
        + " ".join(f"{x:.1f},{y:.1f}" for x, y in scope)
        + '"/>'
    )
    line = [xy(i, r) for i, (_, _, r) in enumerate(points)]
    parts.append(
        f'<polyline fill="none" stroke="{BLUE}" stroke-width="2.5" points="'
        + " ".join(f"{x:.1f},{y:.1f}" for x, y in line)
        + '"/>'
    )
    for (x, y), (_, _, remaining) in zip(line, points, strict=True):
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{BLUE}"/>')
        parts.append(
            f'<text x="{x:.1f}" y="{y - 9:.1f}" font-size="11" text-anchor="middle" '
            f'fill="{INK}">{remaining}</text>'
        )
    for label, colour, offset in (("remaining", BLUE, 0), ("scope", GREY, 80)):
        parts.append(
            f'<text x="{W - RIGHT - offset}" y="{TOP - 8}" font-size="12" text-anchor="end" '
            f'fill="{colour}">{label}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def velocity_svg(milestones: list[Milestone]) -> str:
    parts, step, scale = frame(
        "Velocity by milestone",
        "Story points delivered by each milestone; the outline is the size it was given. "
        "The dashed line is the mean of M1 to M8.",
        roof(max(m.size for m in milestones)),
        [m.key for m in milestones],
    )
    base = H - BOTTOM
    width = step * 0.5
    for i, m in enumerate(milestones):
        x = LEFT + step * (i + 0.5) - width / 2
        parts.append(
            f'<rect x="{x:.1f}" y="{base - m.size * scale:.1f}" width="{width:.1f}" '
            f'height="{m.size * scale:.1f}" fill="none" stroke="{GREY}" stroke-width="1.5"/>'
        )
        parts.append(
            f'<rect x="{x:.1f}" y="{base - m.delivered * scale:.1f}" width="{width:.1f}" '
            f'height="{m.delivered * scale:.1f}" fill="{BLUE}"/>'
        )
        parts.append(
            f'<text x="{x + width / 2:.1f}" y="{base - m.size * scale - 6:.1f}" font-size="11" '
            f'text-anchor="middle" fill="{INK}">{m.delivered}</text>'
        )
    earlier = [m for m in milestones if m.key != "M9"]
    if earlier:
        mean = sum(m.delivered for m in earlier) / len(earlier)
        y = base - mean * scale
        parts.append(
            f'<line x1="{LEFT}" y1="{y:.1f}" x2="{W - RIGHT}" y2="{y:.1f}" stroke="{ORANGE}" '
            'stroke-width="1.5" stroke-dasharray="5 4"/>'
        )
        parts.append(
            f'<text x="{W - RIGHT}" y="{y - 5:.1f}" font-size="11" text-anchor="end" '
            f'fill="{ORANGE}">mean {mean:.1f}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def csv(milestones: list[Milestone]) -> str:
    rows = ["milestone,base,added,size,delivered,scope,remaining"]
    for m, (_, scope, remaining) in zip(milestones, burndown(milestones)[1:], strict=True):
        rows.append(f"{m.key},{m.base},{m.added},{m.size},{m.delivered},{scope},{remaining}")
    return "\n".join(rows) + "\n"


def outputs(text: str) -> dict[str, str]:
    milestones = parse(text)
    return {
        "burndown.svg": burndown_svg(milestones),
        "velocity.svg": velocity_svg(milestones),
        "data.csv": csv(milestones),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    made = outputs(BACKLOG.read_text(encoding="utf8"))
    if args.check:
        stale = [
            name
            for name, text in made.items()
            if not (args.dest / name).is_file()
            or (args.dest / name).read_text(encoding="utf8") != text
        ]
        print("stale: " + (", ".join(stale) if stale else "none"))
        return 1 if stale else 0
    args.dest.mkdir(parents=True, exist_ok=True)
    for name, text in made.items():
        (args.dest / name).write_text(text, encoding="utf8", newline="\n")
        print(f"wrote {args.dest / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
