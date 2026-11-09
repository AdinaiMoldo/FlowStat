"""The graph of a plan, for the preview of the editor (US-28).

One node per step instance and one arrow per entry of ``needs`` of an instance. The layout is the
business of the client; the server gives what a test can check without a browser: the layer of a
node (the length of the longest path that reaches it), the group of a fan-out, the conditional
steps and the goals.
"""

from __future__ import annotations

from dataclasses import dataclass

from flowstat.compiler.plan import Plan, PlanStep
from flowstat.dsl.ast import to_source


@dataclass(frozen=True, slots=True)
class GraphNode:
    id: str  # "publish" or "load[BIS]"
    base: str  # the step of the text this node comes from
    action: str
    line: int  # where the step is in the text (the source map)
    column: int
    layer: int
    conditional: bool  # the step has a `when`
    when: str | None
    goal: bool
    group: str | None  # the fan-out step this node is an instance of
    join: str | None  # told only when it is not the default (all_success)


@dataclass(frozen=True, slots=True)
class GraphEdge:
    source: str
    target: str


@dataclass(frozen=True, slots=True)
class GraphGroup:
    id: str
    size: int


@dataclass(frozen=True, slots=True)
class Graph:
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    groups: tuple[GraphGroup, ...]


def _join(step: PlanStep) -> str | None:
    kind = step.join.kind
    if kind == "all_success":
        return None
    return f"{kind} {step.join.k}" if kind == "at_least" else kind


def _shown(text: str) -> str:
    """``to_source`` wraps every operation in parentheses; drop the pair around the whole."""
    if not (text.startswith("(") and text.endswith(")")):
        return text
    depth = 0
    quote = ""
    for i, char in enumerate(text):
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0 and i < len(text) - 1:
                return text  # the first parenthesis closes before the end: not one pair
    return text[1:-1]


def build_graph(plan: Plan) -> Graph:
    """The graph of ``plan``; nodes keep the (topological) order of the plan."""
    goals = set(plan.goals)
    layer: dict[str, int] = {}
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    sizes: dict[str, int] = {}
    for step in plan.steps:  # topological: every need has its layer already
        layer[step.id] = 1 + max((layer[n] for n in step.needs), default=-1)
        group = step.base if step.loop is not None else None
        if group is not None:
            sizes[group] = sizes.get(group, 0) + 1
        nodes.append(
            GraphNode(
                id=step.id,
                base=step.base,
                action=step.action,
                line=step.line,
                column=step.column,
                layer=layer[step.id],
                conditional=step.when is not None,
                when=_shown(to_source(step.when)) if step.when is not None else None,
                goal=step.id in goals or step.base in goals,
                group=group,
                join=_join(step),
            )
        )
        edges.extend(GraphEdge(need, step.id) for need in step.needs)
    return Graph(tuple(nodes), tuple(edges), tuple(GraphGroup(g, n) for g, n in sizes.items()))
