"""The graph of a plan: one node per step instance, one arrow per dependency (US-28)."""

from __future__ import annotations

from flowstat.compiler.catalogue import Catalogue
from flowstat.compiler.compile import compile_workflow
from flowstat.compiler.plan import Plan
from flowstat.service.graph import Graph, build_graph
from tests.unit.api.helpers import EXAMPLE, REGIONS


def plan_of(text: str, regions: int = 9) -> Plan:
    catalogue = Catalogue({"regions": tuple(REGIONS[:regions])})
    plan = compile_workflow(text, catalogue).plan
    assert plan is not None
    return plan


def example(regions: int = 9) -> Graph:
    # with fewer regions the quorum of seven could never be met, which the compiler refuses
    text = EXAMPLE if regions == 9 else EXAMPLE.replace("at_least: 7", f"at_least: {regions}")
    return build_graph(plan_of(text, regions))


def test_every_step_instance_is_a_node_and_every_dependency_an_arrow() -> None:
    graph = example()
    ids = [n.id for n in graph.nodes]
    assert len(ids) == 22 and len(set(ids)) == 22
    assert {"load[BIS]", "check[CHU]", "consolidate", "approve", "publish"} <= set(ids)
    edges = {(e.source, e.target) for e in graph.edges}
    assert len(graph.edges) == len(edges) == 21
    assert ("load[BIS]", "check[BIS]") in edges
    assert all((f"check[{c['code']}]", "consolidate") in edges for c in [{"code": "BIS"}])
    assert sum(1 for s, t in edges if t == "consolidate") == 9
    assert ("approve", "publish") in edges and ("approve", "return_to_regions") in edges
    assert all(s in ids and t in ids for s, t in edges)


def test_the_nodes_keep_the_order_of_the_plan() -> None:
    plan = plan_of(EXAMPLE)
    assert [n.id for n in build_graph(plan).nodes] == [s.id for s in plan.steps]


def test_a_layer_is_the_length_of_the_longest_path_that_reaches_the_step() -> None:
    layers = {n.id: n.layer for n in example().nodes}
    assert layers["load[BIS]"] == 0
    assert layers["check[BIS]"] == 1
    assert layers == layers | {
        "consolidate": 2,
        "approve": 3,
        "publish": 4,
        "return_to_regions": 4,
    }


def test_an_arrow_always_goes_to_a_later_layer() -> None:
    graph = example()
    layer = {n.id: n.layer for n in graph.nodes}
    assert all(layer[e.source] < layer[e.target] for e in graph.edges)


def test_a_step_with_a_longer_and_a_shorter_way_takes_the_longer() -> None:
    text = """
workflow: w
version: 1
inputs: {}
steps:
  a: {action: approval.wait, with: {role: x}}
  b: {action: approval.wait, needs: [a], with: {role: x}}
  c: {action: approval.wait, needs: [a, b], with: {role: x}}
goals: {all_of: [c]}
"""
    layers = {n.id: n.layer for n in build_graph(plan_of(text)).nodes}
    assert layers == {"a": 0, "b": 1, "c": 2}


def test_conditional_steps_are_marked_and_say_their_condition() -> None:
    nodes = {n.id: n for n in example().nodes}
    marked = sorted(i for i, n in nodes.items() if n.conditional)
    assert marked == ["publish", "return_to_regions"]
    assert nodes["publish"].when == "${approve.decision} == 'approved'"
    assert nodes["approve"].when is None


def test_the_goals_are_marked() -> None:
    marked = sorted(n.id for n in example().nodes if n.goal)
    assert marked == ["publish", "return_to_regions"]


def test_fan_out_instances_are_grouped_under_the_step_they_come_from() -> None:
    graph = example()
    assert [(g.id, g.size) for g in graph.groups] == [("load", 9), ("check", 9)]
    nodes = {n.id: n for n in graph.nodes}
    assert nodes["load[OSC]"].group == "load"
    assert nodes["check[OSC]"].group == "check"
    assert nodes["consolidate"].group is None
    assert nodes["load[OSC]"].base == "load"


def test_the_size_of_a_group_follows_the_catalogue() -> None:
    assert [(g.id, g.size) for g in example(3).groups] == [("load", 3), ("check", 3)]
    assert len(example(3).nodes) == 3 + 3 + 4


def test_the_parentheses_of_a_condition_are_shown_only_where_they_mean_something() -> None:
    from flowstat.service.graph import _shown

    assert _shown("(a == 1)") == "a == 1"
    assert _shown("((a == 1) and (b == 2))") == "(a == 1) and (b == 2)"
    assert _shown("(a == 1) and (b == 2)") == "(a == 1) and (b == 2)"
    assert _shown("(x == ')')") == "x == ')'"
    assert _shown("a == 1") == "a == 1"


def test_the_join_is_told_only_when_it_is_not_the_default() -> None:
    nodes = {n.id: n for n in example().nodes}
    assert nodes["consolidate"].join == "at_least 7"
    assert nodes["approve"].join is None


def test_a_node_knows_its_action_and_its_place_in_the_text() -> None:
    nodes = {n.id: n for n in example().nodes}
    assert nodes["approve"].action == "approval.wait"
    assert nodes["approve"].line == 22
    assert nodes["load[BIS]"].line == nodes["load[OSC]"].line  # the source map of an instance
