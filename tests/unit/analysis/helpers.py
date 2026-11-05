"""Shared helpers of the tests of the analysis: small workflows and random ones."""

from __future__ import annotations

import random

from flowstat.analysis.signatures import ActionSignature, Choices, Signatures
from flowstat.compiler.catalogue import Catalogue
from flowstat.compiler.compile import compile_workflow
from flowstat.compiler.plan import Plan

CATALOGUE = Catalogue({"regions": ({"code": "A", "name": "a"}, {"code": "B", "name": "b"})})
SIGNATURES: Signatures = {
    "approval.wait": ActionSignature(
        {}, {"decision": Choices(("approved", "rejected"), "decisions")}, waits=True
    ),
    "stats.aggregate": ActionSignature({"rows": "list", "regions": "int"}),
}
INPUTS = (
    "  amount: {type: int, min: 0, max: 100}\n"
    "  mode: {type: string, values: [fast, slow, off]}\n"
    "  ratio: {type: float, min: 0, max: 1}\n"
    "  flag: {type: bool}\n"
)


def compile_plan(steps: str, goal: str, inputs: str = INPUTS) -> Plan:
    head = "workflow: demo\nversion: 1\n" + ("inputs:\n" + inputs if inputs else "")
    result = compile_workflow(
        head + "steps:\n" + steps + f"goals: {{any_of: [{goal}]}}\n", CATALOGUE
    )
    assert result.plan is not None, [(d.code, d.message) for d in result.diagnostics]
    return result.plan


def step(
    name: str,
    when: str | None = None,
    needs: list[str] | None = None,
    join: str | None = None,
    action: str = "x.y",
    extra: str = "",
) -> str:
    out = f"  {name}:\n    action: {action}\n"
    if needs:
        out += "    needs: [" + ", ".join(f'"{n}"' for n in needs) + "]\n"
    if join:
        out += f"    join: {join}\n"
    if when is not None:
        out += f'    when: "{when}"\n'
    return out + extra


GUARDS = [
    "${inputs.amount} > {a}",
    "${inputs.amount} <= {a}",
    "${inputs.amount} == {a}",
    "${inputs.mode} == '{m}'",
    "${inputs.mode} != '{m}'",
    "${inputs.mode} in ['{m}', '{n}']",
    "${inputs.flag}",
    "not ${inputs.flag}",
    "${inputs.ratio} < {r}",
    "${inputs.ratio} >= {r}",
    "${inputs.amount} > {a} and ${inputs.flag}",
    "${inputs.amount} < {a} or ${inputs.mode} == '{m}'",
]


def random_guard(rng: random.Random) -> str:
    text = rng.choice(GUARDS)
    return (
        text.replace("{a}", str(rng.choice([0, 25, 50, 75, 100])))
        .replace("{m}", rng.choice(["fast", "slow", "off"]))
        .replace("{n}", rng.choice(["fast", "slow", "off"]))
        .replace("{r}", rng.choice(["0.25", "0.5", "0.75"]))
    )


def random_workflow(
    rng: random.Random, guard_chance: float = 0.5, continue_chance: float = 0.0
) -> Plan:
    """A random acyclic workflow with every join rule and guards on the declared inputs."""
    n = rng.randint(2, 7)
    names = [f"s{i}" for i in range(n)]
    steps = ""
    for i, name in enumerate(names):
        needs = rng.sample(names[:i], rng.randint(0, min(3, i))) if i else []
        join = None
        if needs:
            kind = rng.choice(["all_success", "any_success", "all_done", "at_least"])
            join = f"{{at_least: {rng.randint(1, len(needs))}}}" if kind == "at_least" else kind
        when = random_guard(rng) if rng.random() < guard_chance else None
        extra = "    on_failure: continue\n" if rng.random() < continue_chance else ""
        steps += step(name, when, needs, join, extra=extra)
    return compile_plan(steps, names[-1])
