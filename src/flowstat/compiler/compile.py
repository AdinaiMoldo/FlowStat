"""The compiler phases 0 to 3 (C1, level 2): text to a plan, or to diagnostics.

0. expansion of ``foreach`` into one instance per catalogue item, with a source map;
1. symbols: steps, inputs, catalogue lists, goals and actions exist;
2. references: ``needs`` and ``${...}`` are resolved to instances, edges are built;
3. cycles (Tarjan, then the shortest cycle of each component) and ancestor bitsets, which are
   used to check that every step a step reads from is guaranteed to run before it.

All problems of all phases are collected in one run. Nothing here raises on bad input.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from flowstat.compiler.catalogue import Catalogue
from flowstat.compiler.graph import (
    ancestor_bitsets,
    shortest_cycle,
    strongly_connected_components,
    topological_order,
)
from flowstat.compiler.plan import InputSpec, JoinSpec, Plan, PlanStep, RetrySpec
from flowstat.compiler.workflow import NeedRef, Pos, StepDecl, Workflow, build_workflow
from flowstat.dsl.ast import (
    Attr,
    Binary,
    Call,
    Compare,
    Expr,
    Index,
    Literal,
    PathPart,
    Ref,
    Unary,
)
from flowstat.dsl.reader import read_yaml
from flowstat.dsl.shape import check_shape
from flowstat.dsl.tree import Mapping
from flowstat.model.diagnostics import Diagnostic, Severity


@dataclass
class CompileResult:
    plan: Plan | None
    diagnostics: list[Diagnostic]


def rewrite_refs(expr: Expr, fn: Callable[[Ref], Expr]) -> Expr:
    """Rebuild an expression with every reference replaced by ``fn(reference)``."""
    match expr:
        case Ref():
            return fn(expr)
        case Unary(line=ln, column=col, op=op, operand=operand):
            return Unary(ln, col, op, rewrite_refs(operand, fn))
        case Binary(line=ln, column=col, op=op, left=left, right=right):
            return Binary(ln, col, op, rewrite_refs(left, fn), rewrite_refs(right, fn))
        case Compare(line=ln, column=col, op=op, left=left, right=right):
            return Compare(ln, col, op, rewrite_refs(left, fn), rewrite_refs(right, fn))
        case Call(line=ln, column=col, func=func, args=args):
            return Call(ln, col, rewrite_refs(func, fn), tuple(rewrite_refs(a, fn) for a in args))
        case Attr(line=ln, column=col, obj=obj, name=name):
            return Attr(ln, col, rewrite_refs(obj, fn), name)
        case Index(line=ln, column=col, obj=obj, index=index):
            return Index(ln, col, rewrite_refs(obj, fn), rewrite_refs(index, fn))
        case _:
            return expr


@dataclass
class _Instance:
    id: str
    decl: StepDecl
    binding: tuple[str, dict[str, str]] | None  # (loop variable, catalogue item)


@dataclass
class _Use:
    """A place where a step reads another step: checked against the ancestors later."""

    user: str
    used: list[str]
    pos: Pos
    allow_self: bool


class _Compiler:
    def __init__(self, wf: Workflow, catalogue: Catalogue, known_actions: frozenset[str] | None):
        self.wf = wf
        self.catalogue = catalogue
        self.known_actions = known_actions
        self.diags: list[Diagnostic] = []
        self.instances: list[_Instance] = []
        self.by_base: dict[str, list[_Instance]] = {}
        self.foreach_bases: set[str] = set()
        self.failed_bases: set[str] = set()
        self.uses: list[_Use] = []

    def error(self, code: str, message: str, pos: Pos) -> None:
        self.diags.append(Diagnostic(code, Severity.ERROR, message, pos.line, pos.column))

    def check_inputs(self) -> None:
        for decl in self.wf.inputs:
            if decl.ref is None or decl.ref_pos is None:
                continue
            if decl.type not in ("string", "list<string>"):
                self.error(
                    "E-REF-014",
                    f"'ref' needs an input of type string or list<string>, not '{decl.type}'",
                    decl.ref_pos,
                )
            elif decl.ref not in self.catalogue.lists:
                self.error("E-REF-012", f"unknown catalogue list '{decl.ref}'", decl.ref_pos)

    # -- phase 0 and 1: expansion and symbols ----------------------------------------------------
    def expand(self) -> None:
        for decl in self.wf.steps:
            self.by_base[decl.name] = []
            if self.known_actions is not None and decl.action not in self.known_actions:
                self.error("E-REF-008", f"unknown action '{decl.action}'", decl.action_pos)
            if (
                self.known_actions is not None
                and decl.compensate is not None
                and decl.compensate not in self.known_actions
                and decl.compensate_pos is not None
            ):
                self.error(
                    "E-REF-015",
                    f"unknown compensation action '{decl.compensate}'",
                    decl.compensate_pos,
                )
            if decl.foreach is None:
                self.add(_Instance(decl.name, decl, None))
                continue
            self.foreach_bases.add(decl.name)
            items = self.catalogue.lists.get(decl.foreach.list_name)
            if items is None:
                self.error(
                    "E-REF-012",
                    f"unknown catalogue list '{decl.foreach.list_name}'",
                    decl.foreach.pos,
                )
                self.failed_bases.add(decl.name)
            elif not items:
                self.error(
                    "E-REF-003",
                    f"the catalogue list '{decl.foreach.list_name}' is empty, so '{decl.name}' "
                    "would have no instances",
                    decl.foreach.pos,
                )
                self.failed_bases.add(decl.name)
            else:
                for item in items:
                    inst = _Instance(f"{decl.name}[{item['code']}]", decl, (decl.foreach.var, item))
                    self.add(inst)
        self.check_inputs()
        names = {d.name for d in self.wf.steps}
        for goal, pos in self.wf.goals:
            if goal not in names:
                self.error("E-REF-001", f"unknown step '{goal}' in goals", pos)
            elif goal in self.foreach_bases and goal not in self.failed_bases:
                self.error(
                    "E-REF-009",
                    f"'{goal}' is a foreach step; name a step that runs once",
                    pos,
                )

    def add(self, inst: _Instance) -> None:
        self.instances.append(inst)
        self.by_base[inst.decl.name].append(inst)

    # -- phase 2: references ---------------------------------------------------------------------
    def resolve_need(self, inst: _Instance, need: NeedRef) -> list[str]:
        name = need.step
        if name not in self.by_base:
            self.error("E-REF-001", f"unknown step '{name}'", need.pos)
            return []
        if name in self.failed_bases:
            return []
        return self.select(inst, name, need.selector, need.pos)

    def select(self, inst: _Instance, name: str, selector: str | None, pos: Pos) -> list[str]:
        targets = self.by_base[name]
        is_foreach = name in self.foreach_bases
        if selector is None:
            if is_foreach:
                self.error(
                    "E-REF-013",
                    f"'{name}' runs once per item; use '{name}[var]' or '{name}[*]'",
                    pos,
                )
                return []
            return [targets[0].id]
        if selector == "*":
            if not is_foreach:
                self.error("E-REF-006", f"'{name}[*]': '{name}' is not a foreach step", pos)
                return []
            return [t.id for t in targets]
        # a loop variable: the instance with the same catalogue code as the current one
        if inst.binding is None or inst.binding[0] != selector:
            self.error(
                "E-REF-005",
                f"'{selector}' is not a loop variable of the step '{inst.decl.name}'",
                pos,
            )
            return []
        wanted = f"{name}[{inst.binding[1]['code']}]"
        if not is_foreach or wanted not in {t.id for t in targets}:
            self.error("E-REF-005", f"'{name}' has no instance for the item of '{selector}'", pos)
            return []
        return [wanted]

    def resolve_ref(self, inst: _Instance, ref: Ref) -> tuple[Ref | str | None, list[str]]:
        """A reference as the plan keeps it: a loop attribute becomes text, a step becomes ids."""
        pos = Pos(ref.line, ref.column)
        first, rest = ref.path[0], ref.path[1:]
        name = str(first.value)
        if name == "inputs":
            if not rest or str(rest[0].value) not in self.wf.input_names:
                shown = rest[0].value if rest else ""
                self.error("E-REF-001", f"unknown input '{shown}'", pos)
                return None, []
            return ref, []
        if inst.binding is not None and name == inst.binding[0]:
            attr = str(rest[0].value) if len(rest) == 1 and rest[0].kind == "field" else None
            if attr is None or attr not in inst.binding[1]:
                self.error(
                    "E-REF-004",
                    f"'{name}' has no attribute '{attr or ''}'; known: "
                    f"{', '.join(sorted(inst.binding[1]))}",
                    pos,
                )
                return None, []
            return inst.binding[1][attr], []
        if name not in self.by_base:
            self.error("E-REF-001", f"unknown step '{name}'", pos)
            return None, []
        if name in self.failed_bases:
            return None, []
        selector_part = rest[0] if rest and rest[0].kind in ("var", "star", "index") else None
        if selector_part is not None and selector_part.kind == "index":
            self.error(
                "E-REF-011", f"'{name}[{selector_part.value}]': indexing is not supported", pos
            )
            return None, []
        selector = None if selector_part is None else str(selector_part.value)
        ids = self.select(inst, name, selector, pos)
        if not ids:
            return None, []
        if selector_part is not None and selector_part.kind == "var":
            new_path = (PathPart("field", ids[0]), *rest[1:])
            return Ref(ref.line, ref.column, new_path), ids
        return ref, ids

    def resolve_expr(self, inst: _Instance, expr: Expr | None, allow_self: bool) -> Expr | None:
        if expr is None:
            return None

        def replace(ref: Ref) -> Expr:
            new, ids = self.resolve_ref(inst, ref)
            if ids:
                self.uses.append(_Use(inst.id, ids, Pos(ref.line, ref.column), allow_self))
            if isinstance(new, str):
                return Literal(ref.line, ref.column, new)
            return new if new is not None else ref

        return rewrite_refs(expr, replace)

    def resolve_with(self, inst: _Instance) -> tuple[tuple[str, tuple[object, ...]], ...]:
        resolved: list[tuple[str, tuple[object, ...]]] = []
        for key, parts in inst.decl.with_:
            out: list[object] = []
            for part in parts:
                if isinstance(part, Ref):
                    new, ids = self.resolve_ref(inst, part)
                    if ids:
                        self.uses.append(_Use(inst.id, ids, Pos(part.line, part.column), False))
                    out.append(part if new is None else new)
                else:
                    out.append(part)
            resolved.append((key, tuple(out)))
        return tuple(resolved)


def _report_unique(diags: list[Diagnostic]) -> list[Diagnostic]:
    seen: set[tuple[str, int, int, str]] = set()
    unique: list[Diagnostic] = []
    for d in diags:
        key = (d.code, d.line, d.column, d.message)
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return sorted(unique, key=lambda d: (d.line, d.column))


def compile_workflow(
    text: str, catalogue: Catalogue, known_actions: frozenset[str] | None = None
) -> CompileResult:
    tree, diags = read_yaml(text)
    if tree is None:
        return CompileResult(None, diags)
    shape_diags = check_shape(tree)
    if shape_diags or not isinstance(tree, Mapping):
        return CompileResult(None, shape_diags)
    wf, build_diags = build_workflow(tree)
    comp = _Compiler(wf, catalogue, known_actions)
    comp.diags.extend(build_diags)
    comp.expand()

    # phase 2: resolve every reference of every instance
    needs: dict[str, list[str]] = {}
    resolved: dict[str, tuple[Expr | None, Expr | None, tuple[tuple[str, tuple[object, ...]], ...]]]
    resolved = {}
    for inst in comp.instances:
        ids: list[str] = []
        for need in inst.decl.needs:
            for target in comp.resolve_need(inst, need):
                if target not in ids:
                    ids.append(target)
        needs[inst.id] = ids
        resolved[inst.id] = (
            comp.resolve_expr(inst, inst.decl.when, False),
            comp.resolve_expr(inst, inst.decl.ensures, True),
            comp.resolve_with(inst),
        )
        decl = inst.decl
        broken = any(n.step in comp.failed_bases for n in decl.needs)  # already reported
        if (
            decl.join_kind == "at_least"
            and decl.join_k_pos
            and decl.join_k > len(ids)
            and not broken
        ):
            comp.error(
                "E-REF-010",
                f"at_least {decl.join_k} but '{decl.name}' has only {len(ids)} dependencies",
                decl.join_k_pos,
            )

    # phase 3: cycles, order, ancestors
    index = {inst.id: i for i, inst in enumerate(comp.instances)}
    n = len(comp.instances)
    needs_adj: list[list[int]] = [[index[t] for t in needs[inst.id]] for inst in comp.instances]
    cyclic = False
    for component in strongly_connected_components(needs_adj):
        if len(component) == 1 and component[0] not in needs_adj[component[0]]:
            continue
        cyclic = True
        cycle = shortest_cycle(needs_adj, component)
        start = cycle.index(min(cycle))
        cycle = cycle[start:] + cycle[:start]
        names = [comp.instances[i].id for i in cycle]
        comp.error(
            "E-GRAPH-001",
            "dependency cycle: " + " -> ".join([*names, names[0]]),
            comp.instances[cycle[0]].decl.pos,
        )
    plan: Plan | None = None
    if not cyclic:
        dependents: list[list[int]] = [[] for _ in range(n)]
        for i, deps in enumerate(needs_adj):
            for d in deps:
                dependents[d].append(i)
        order = topological_order(dependents)
        assert order is not None
        position = {idx: p for p, idx in enumerate(order)}
        pos_adj: list[list[int]] = [[] for _ in range(n)]
        for d, outs in enumerate(dependents):
            pos_adj[position[d]] = sorted(position[o] for o in outs)
        ancestors = ancestor_bitsets(pos_adj, list(range(n)))
        for use in comp.uses:
            user_p = position[index[use.user]]
            for used in use.used:
                used_p = position[index[used]]
                if use.allow_self and used_p == user_p:
                    continue
                if not ancestors[user_p] >> used_p & 1:
                    base = comp.instances[index[used]].decl.name
                    comp.error(
                        "E-REF-007",
                        f"'{base}' is used here but is not a dependency of "
                        f"'{comp.instances[index[use.user]].decl.name}'; add it to 'needs'",
                        use.pos,
                    )
        steps: list[PlanStep] = []
        for idx in order:
            inst = comp.instances[idx]
            when, ensures, with_ = resolved[inst.id]
            decl = inst.decl
            steps.append(
                PlanStep(
                    id=inst.id,
                    base=decl.name,
                    action=decl.action,
                    needs=tuple(needs[inst.id]),
                    join=JoinSpec(decl.join_kind, decl.join_k),
                    when=when,
                    ensures=ensures,
                    with_=with_,
                    on_failure=decl.on_failure,
                    retry=RetrySpec(*decl.retry) if decl.retry else None,
                    compensate=decl.compensate,
                    loop=None
                    if inst.binding is None
                    else (inst.binding[0], inst.binding[1]["code"]),
                    line=decl.pos.line,
                    column=decl.pos.column,
                )
            )
        plan = Plan(
            wf.name,
            wf.version,
            tuple(steps),
            wf.goal_kind,
            tuple(g for g, _ in wf.goals),
            tuple(ancestors),
            tuple(
                InputSpec(
                    i.name,
                    i.type,
                    i.pattern,
                    i.ref,
                    i.has_default,
                    i.default,
                    i.required,
                    i.pos.line,
                    i.pos.column,
                )
                for i in wf.inputs
            ),
        )
    final = _report_unique(comp.diags)
    return CompileResult(plan if not final else None, final)
