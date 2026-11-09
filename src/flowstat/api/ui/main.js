// The editor page: workflows, a text with live diagnostics, save and publish.
import { ApiError, call, session } from "./api.js";
import { PAUSE_MS, createEditor } from "./editor.js";
import { createGraph } from "./graph.js";
import { createMigration } from "./migration.js";
import { createOps } from "./ops.js";

const $ = (id) => document.getElementById(id);

// A new workflow starts from the smallest text that passes the checks.
const SKELETON = `workflow: new_workflow
version: 1
inputs:
  quarter: {type: string}
steps:
  approve:
    action: approval.wait
    with: {role: chief_statistician}
goals: {all_of: [approve]}
`;

const state = {
  editor: null,
  current: null, // { name, number, status } of the version that was saved or opened
  checked: null, // the text that was last sent to /validate
  sequence: 0, // answers that arrive late for an older text are dropped
  timer: null,
  problems: [],
  preview: false, // the graph is shown
  graph: null, // the drawing, made when the graph is first shown
  graphSequence: 0,
  hasGraph: false, // a valid text was drawn before, so the last graph can stay on an error
};

function nameOf(text) {
  const found = /^workflow:\s*([A-Za-z_][A-Za-z0-9_]*)/m.exec(text);
  return found ? found[1] : null;
}

function say(text) {
  $("status").textContent = text;
}

function explain(error) {
  if (error instanceof ApiError) {
    if (error.status === 401) return "sign in: paste a token and press Use";
    if (error.status === 403) return "this token may not edit workflows (a designer or an admin may)";
    if (error.status === 0) return "the server cannot be reached";
    return error.message;
  }
  return String(error);
}

// -- problems --------------------------------------------------------------------------------
function show(problems) {
  state.problems = problems;
  const list = $("problems");
  list.replaceChildren();
  for (const p of problems) {
    const item = document.createElement("li");
    item.className = `problem ${p.severity}`;
    item.dataset.testid = "problem";
    item.dataset.code = p.code;
    const where = document.createElement("span");
    where.className = "where";
    where.textContent = p.line ? `${p.line}:${p.column}` : "";
    const code = document.createElement("span");
    code.className = "code";
    code.textContent = p.code;
    const message = document.createElement("span");
    message.className = "message";
    message.textContent = p.message;
    item.append(where, code, message);
    if (p.line) item.addEventListener("click", () => state.editor.reveal(p.line, p.column));
    list.append(item);
  }
}

function summary(errors, findings) {
  const part = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  if (!errors && !findings) return "no problems";
  return [errors && part(errors, "error"), findings && part(findings, "finding")]
    .filter(Boolean)
    .join(", ");
}

// -- the graph ---------------------------------------------------------------------------------
async function drawGraph(text) {
  const mine = ++state.graphSequence;
  const note = $("graph-note");
  try {
    const result = await call("POST", "/graph", { text });
    if (mine !== state.graphSequence) return;
    if (result.graph) {
      state.graph.draw(result.graph);
      state.hasGraph = true;
      note.hidden = true;
    } else {
      note.hidden = false;
      note.textContent = state.hasGraph
        ? "The text has errors; this is the last graph that was valid."
        : "The text has errors, so there is no graph yet.";
    }
  } catch (error) {
    if (mine !== state.graphSequence) return;
    note.hidden = false;
    note.textContent = explain(error);
  }
}

function togglePreview() {
  state.preview = !state.preview;
  $("graph-pane").hidden = !state.preview;
  $("preview").textContent = state.preview ? "Hide graph" : "Show graph";
  $("preview").setAttribute("aria-pressed", String(state.preview));
  if (state.preview) {
    state.graph ||= createGraph($("graph"), (line, column) => state.editor.reveal(line, column));
    drawGraph(state.editor.getText());
  }
}

// -- the check after a pause -----------------------------------------------------------------
async function check() {
  const text = state.editor.getText();
  if (text === state.checked) return;
  state.checked = text;
  const mine = ++state.sequence;
  say("checking…");
  if (state.preview) drawGraph(text);
  try {
    const result = await call("POST", "/validate", { text });
    if (mine !== state.sequence) return;
    const errors = result.diagnostics.map((d) => ({ ...d, severity: "error" }));
    const items = [...errors, ...result.findings].sort((a, b) => a.line - b.line || a.column - b.column);
    state.editor.setMarkers(items);
    show(items);
    say(summary(errors.length, result.findings.length));
  } catch (error) {
    if (mine !== state.sequence) return;
    state.checked = null; // try again at the next change
    say(explain(error));
  }
}

function changed() {
  clearTimeout(state.timer);
  state.timer = setTimeout(check, PAUSE_MS);
  $("save").disabled = nameOf(state.editor.getText()) === null;
}

// -- workflows -------------------------------------------------------------------------------
function describe() {
  const c = state.current;
  $("name").textContent = c ? c.name : nameOf(state.editor.getText()) || "no workflow";
  $("state").textContent = c ? `v${c.number} ${c.status}` : "not saved";
  $("publish").disabled = !(c && c.status === "draft");
}

async function refresh() {
  const list = $("workflows");
  try {
    const items = await call("GET", "/workflows");
    list.replaceChildren();
    for (const w of items) {
      const item = document.createElement("li");
      item.textContent = w.name;
      item.title = `latest v${w.latest}` + (w.latest_published ? `, published v${w.latest_published}` : "");
      if (state.current && state.current.name === w.name) item.classList.add("current");
      item.addEventListener("click", () => open(w.name));
      list.append(item);
    }
  } catch (error) {
    list.replaceChildren();
    say(explain(error));
  }
}

async function open(name) {
  try {
    const versions = await call("GET", `/workflows/${encodeURIComponent(name)}/versions`);
    const last = versions[versions.length - 1];
    const detail = await call("GET", `/workflows/${encodeURIComponent(name)}/versions/${last.number}`);
    state.current = { name, number: last.number, status: last.status };
    state.editor.setText(detail.text);
    state.checked = null;
    describe();
    await check();
    refresh();
  } catch (error) {
    say(explain(error));
  }
}

async function save() {
  const text = state.editor.getText();
  const name = nameOf(text);
  if (!name) return;
  try {
    const saved = await call("POST", `/workflows/${encodeURIComponent(name)}/versions`, { text });
    state.current = { name, number: saved.version, status: saved.status };
    describe();
    say(`saved as v${saved.version}`);
    refresh();
  } catch (error) {
    say(explain(error));
  }
}

async function publish() {
  const c = state.current;
  if (!c) return;
  try {
    const done = await call("POST", `/workflows/${encodeURIComponent(c.name)}/versions/${c.number}/publish`);
    state.current = { ...c, status: done.status };
    describe();
    say(`v${c.number} is published`);
    refresh();
  } catch (error) {
    if (error instanceof ApiError && error.status === 422) {
      show(error.errors.map((e) => ({ severity: "error", line: e.line, column: e.column, ...e })));
      say("not published");
    } else {
      say(explain(error));
    }
  }
}

function fresh() {
  state.current = null;
  state.editor.setText(SKELETON);
  state.checked = null;
  describe();
  check();
}

// -- views -----------------------------------------------------------------------------------
const VIEWS = ["editor", "runs", "services", "migration"];

function goTo(view) {
  for (const name of VIEWS) {
    $(`view-${name}`).hidden = name !== view;
    $(`tab-${name}`).setAttribute("aria-selected", String(name === view));
  }
  document.body.dataset.view = view;
  if (view === "runs") state.ops.loadRuns();
  if (view === "services") state.ops.loadServices();
  if (view === "migration") state.migration.load();
}

// -- start -----------------------------------------------------------------------------------
async function main() {
  state.ops = createOps({ goTo });
  state.migration = createMigration();
  for (const name of VIEWS) $(`tab-${name}`).addEventListener("click", () => goTo(name));
  const plain = new URLSearchParams(location.search).has("plain");
  state.editor = await createEditor($("editor"), $("plain"), plain);
  if (state.editor.kind === "plain") {
    const note = $("notice");
    note.hidden = false;
    note.textContent = plain
      ? "Plain text mode."
      : "The editor is not installed here, so this is a plain text area. Run: python scripts/fetch_ui.py";
  }
  state.editor.onChange(changed);
  if (state.editor.registerCompletion) {
    state.editor.registerCompletion(async (text, line, column) => {
      try {
        return await call("POST", "/complete", { text, line, column });
      } catch (error) {
        say(explain(error));
        return null;
      }
    });
  }

  $("sign-in").addEventListener("submit", (event) => {
    event.preventDefault();
    session.set($("token").value.trim());
    $("token").value = "";
    $("who").textContent = "token kept for this tab";
    state.checked = null;
    refresh();
    check();
  });
  $("forget").addEventListener("click", () => {
    session.clear();
    $("who").textContent = "";
    $("workflows").replaceChildren();
  });
  $("new").addEventListener("click", fresh);
  $("preview").addEventListener("click", togglePreview);
  $("save").addEventListener("click", save);
  $("publish").addEventListener("click", publish);

  if (session.token) {
    $("who").textContent = "token kept for this tab";
    refresh();
  }
  describe();
  document.body.dataset.ready = state.editor.kind;
}

main();
