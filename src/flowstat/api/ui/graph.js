// The preview of a workflow as a graph (US-28). The server gives nodes, arrows, layers and groups
// (POST /graph); this file draws them with Cytoscape and dagre when they were fetched
// (scripts/fetch_ui.py), and as a list of layers when they were not.

export const COLLAPSE_OVER = 12; // a fan-out with more instances than this starts collapsed

// The elements to draw for a graph. A collapsed fan-out is one node; an expanded one is a group
// that holds its instances. Arrows between nodes that end up in the same collapsed node are dropped
// and equal arrows are drawn once.
export function elementsOf(graph, collapsed) {
  const sizes = new Map(graph.groups.map((g) => [g.id, g.size]));
  const byGroup = new Map();
  for (const n of graph.nodes) {
    if (n.group) byGroup.set(n.group, [...(byGroup.get(n.group) || []), n]);
  }
  const shown = (id, group) => (group && collapsed.has(group) ? `group:${group}` : id);
  const owner = new Map(graph.nodes.map((n) => [n.id, n.group]));
  const elements = [];
  const done = new Set();
  const flags = (nodes) =>
    [nodes.some((n) => n.conditional) && "conditional", nodes.some((n) => n.goal) && "goal"]
      .filter(Boolean)
      .join(" ");
  for (const n of graph.nodes) {
    if (n.group && collapsed.has(n.group)) {
      if (done.has(n.group)) continue;
      done.add(n.group);
      const members = byGroup.get(n.group);
      elements.push({
        data: {
          id: `group:${n.group}`,
          label: `${n.group} ×${sizes.get(n.group)}\n${n.action}`,
          group: n.group,
          line: n.line,
          column: n.column,
        },
        classes: `collapsed ${flags(members)}`.trim(),
      });
      continue;
    }
    if (n.group && !done.has(`parent:${n.group}`)) {
      done.add(`parent:${n.group}`);
      elements.push({
        data: { id: `parent:${n.group}`, label: `${n.group} ×${sizes.get(n.group)}`, group: n.group },
        classes: "fan-out",
      });
    }
    elements.push({
      data: {
        id: n.id,
        parent: n.group ? `parent:${n.group}` : undefined,
        label: `${n.id}\n${n.action}`,
        title: [n.when && `when ${n.when}`, n.join && `join ${n.join}`].filter(Boolean).join("; "),
        line: n.line,
        column: n.column,
      },
      classes: flags([n]),
    });
  }
  const seen = new Set();
  for (const e of graph.edges) {
    const source = shown(e.source, owner.get(e.source));
    const target = shown(e.target, owner.get(e.target));
    const key = `${source}->${target}`;
    if (source === target || seen.has(key)) continue;
    seen.add(key);
    elements.push({ data: { id: key, source, target } });
  }
  return elements;
}

// The colour class of a step state, for the graph of a run (US-44).
export function stateClass(state) {
  switch (state) {
    case "SUCCEEDED":
      return "st-ok";
    case "RUNNING":
    case "READY":
    case "WAITING":
    case "COMPENSATING":
      return "st-wait";
    case "FAILED":
    case "UPSTREAM_FAILED":
    case "UNCERTAIN":
    case "COMPENSATION_FAILED":
      return "st-bad";
    case "SKIPPED":
    case "CANCELLED":
    case "COMPENSATED":
      return "st-skip";
    default:
      return "";
  }
}

// One class for a collapsed fan-out, from the classes of its instances: a failure shows, then
// what is still going; otherwise it is green when anything succeeded and grey when all were skipped.
export function groupClass(classes) {
  const known = classes.filter(Boolean);
  if (known.length === 0) return "";
  for (const wanted of ["st-bad", "st-wait"]) if (known.includes(wanted)) return wanted;
  if (known.length < classes.length) return ""; // some instances have no state yet
  return known.includes("st-ok") ? "st-ok" : "st-skip";
}

const STYLE = [
  {
    selector: "node",
    style: {
      shape: "round-rectangle",
      label: "data(label)",
      "text-wrap": "wrap",
      "text-valign": "center",
      "text-halign": "center",
      "font-size": 10,
      color: "#1d2433",
      "background-color": "#ffffff",
      "border-width": 1,
      "border-color": "#98a2b3",
      width: "label",
      height: "label",
      padding: "8px",
    },
  },
  { selector: "node.conditional", style: { "border-style": "dashed", "border-color": "#a15c07" } },
  { selector: "node.goal", style: { "border-width": 3, "border-color": "#1f5fbf" } },
  { selector: "node.collapsed", style: { "background-color": "#eef2f9", "border-width": 2 } },
  { selector: "node.st-ok", style: { "background-color": "#ecfdf3", "border-color": "#067647" } },
  { selector: "node.st-wait", style: { "background-color": "#fffaeb", "border-color": "#a15c07" } },
  { selector: "node.st-bad", style: { "background-color": "#fef3f2", "border-color": "#b42318" } },
  {
    selector: "node.st-skip",
    style: { "background-color": "#f2f4f7", color: "#667085", "border-style": "dashed" },
  },
  {
    selector: "node.fan-out",
    style: {
      "background-color": "#f6f7f9",
      "background-opacity": 0.6,
      "border-style": "dotted",
      "border-color": "#667085",
      "text-valign": "top",
      "text-margin-y": -4,
      color: "#667085",
      padding: "10px",
    },
  },
  {
    selector: "edge",
    style: {
      width: 1,
      "line-color": "#98a2b3",
      "target-arrow-color": "#98a2b3",
      "target-arrow-shape": "triangle",
      "curve-style": "bezier",
      "arrow-scale": 0.8,
    },
  },
];

// -- without the library: the layers as text ---------------------------------------------------
function listOf(host, graph) {
  const layers = new Map();
  for (const n of graph.nodes) layers.set(n.layer, [...(layers.get(n.layer) || []), n]);
  const list = document.createElement("ol");
  list.className = "layers";
  for (const [layer, nodes] of [...layers].sort((a, b) => a[0] - b[0])) {
    const item = document.createElement("li");
    const names = [];
    const groups = new Set();
    for (const n of nodes) {
      if (n.group) {
        if (groups.has(n.group)) continue;
        groups.add(n.group);
        names.push(`${n.group} ×${graph.groups.find((g) => g.id === n.group).size}`);
      } else {
        names.push(n.conditional ? `${n.id} (conditional)` : n.id);
      }
    }
    item.textContent = `layer ${layer}: ${names.join(", ")}`;
    list.append(item);
  }
  host.replaceChildren(list);
}

// ``collapseOver``: a fan-out with more instances than this starts collapsed (the graph of a run
// uses 1, so that it can be read at a glance).
export function createGraph(host, onSelect, { collapseOver = COLLAPSE_OVER } = {}) {
  const capable = typeof window.cytoscape === "function" && window.cytoscapeDagre;
  const collapsed = new Set();
  const known = new Set(); // groups seen once, so that a collapse chosen by the person stays
  let last = null;

  let painted = null; // step -> state of the run that is shown, or null for a plain graph

  if (!capable) {
    return { kind: "list", draw: (graph) => listOf(host, graph), paint: () => {} };
  }
  if (!window.cytoscape.__flowstatDagre) {
    window.cytoscape.use(window.cytoscapeDagre);
    window.cytoscape.__flowstatDagre = true;
  }
  const cy = window.cytoscape({
    container: host,
    elements: [],
    style: STYLE,
    wheelSensitivity: 0.3,
    minZoom: 0.15,
    maxZoom: 2.5,
  });
  host.cy = cy; // the tests read the drawn graph from here

  // The colours of the states of a run. A collapsed fan-out takes one colour from its instances.
  function colour() {
    cy.nodes().forEach((node) => {
      node.removeClass("st-ok st-wait st-bad st-skip");
      if (!painted || node.hasClass("fan-out")) return;
      let found;
      if (node.hasClass("collapsed")) {
        const group = node.data("group");
        found = groupClass(
          last.nodes.filter((n) => n.group === group).map((n) => stateClass(painted.get(n.id))),
        );
      } else {
        found = stateClass(painted.get(node.id()));
      }
      if (found) node.addClass(found);
    });
  }

  function render() {
    cy.elements().remove();
    cy.add(elementsOf(last, collapsed));
    cy.layout({
      name: "dagre",
      rankDir: "TB",
      nodeSep: 14,
      rankSep: 34,
      fit: true,
      padding: 12,
      animate: false,
    }).run();
    colour();
  }

  cy.on("tap", "node", (event) => {
    const node = event.target;
    const group = node.data("group");
    if (group && (node.hasClass("collapsed") || node.hasClass("fan-out"))) {
      if (collapsed.has(group)) collapsed.delete(group);
      else collapsed.add(group);
      render();
    } else if (node.data("line")) {
      onSelect(node.data("line"), node.data("column") || 1);
    }
  });
  cy.on("mouseover", "node", (event) => {
    host.title = event.target.data("title") || "";
  });
  cy.on("mouseout", "node", () => {
    host.title = "";
  });

  return {
    kind: "cytoscape",
    cy,
    draw(graph) {
      last = graph;
      for (const g of graph.groups) {
        if (!known.has(g.id)) {
          known.add(g.id);
          if (g.size > collapseOver) collapsed.add(g.id);
        }
      }
      render();
    },
    // states: a Map from the name of a step to its state; null clears the colours
    paint(states) {
      painted = states;
      colour();
    },
    fit: () => cy.fit(undefined, 12),
  };
}
