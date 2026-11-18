// The page: signing in, the addresses of the pages, and the editor (workflows, a text with live
// diagnostics, save and publish). The other pages are in their own files.
import { ApiError, call, session, whenUnauthorized } from "./api.js";
import { PAUSE_MS, createEditor } from "./editor.js";
import { EXAMPLE } from "./example.js";
import { createForms } from "./forms.js";
import { createGraph } from "./graph.js";
import { createHome, displayName, roleLabel } from "./home.js";
import { createMigration } from "./migration.js";
import { createOps } from "./ops.js";
import { href, nextOf, parse } from "./router.js";
import { createStatistics } from "./statistics.js";

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
  user: null, // who is signed in: { name, role, titles, permissions }
  view: null, // the page that is shown
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
    if (error.status === 401) return "your session has ended: sign in again";
    if (error.status === 403) return "you may not edit workflows (a designer or an admin may)";
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

// What the person signed in may do: the permissions the core sent with the user.
function can(permission) {
  return Boolean(state.user && state.user.permissions.includes(permission));
}

// -- the check after a pause -----------------------------------------------------------------
async function check() {
  const text = state.editor.getText();
  if (text === state.checked) return;
  if (!can("workflows:write")) {
    state.checked = text;
    say("read only: your role runs workflows and does not change them");
    return;
  }
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
  $("save").disabled = !can("workflows:write") || nameOf(state.editor.getText()) === null;
}

// -- workflows -------------------------------------------------------------------------------
function describe() {
  const c = state.current;
  $("name").textContent = c ? c.name : nameOf(state.editor.getText()) || "no workflow";
  $("state").textContent = c ? `v${c.number} ${c.status}` : "not saved";
  $("publish").disabled = !(c && c.status === "draft" && can("workflows:publish"));
  $("run").disabled = !(c && c.status === "published");
  $("run").title = $("run").disabled ? "Publish this version first" : "Start a run of this version";
  if ($("run").disabled) $("run-form").hidden = true;
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
      item.addEventListener("click", () => goTo(`editor/${encodeURIComponent(w.name)}`));
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
    keepAddress(href("editor", name));
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

function fresh(text = SKELETON) {
  state.current = null;
  state.editor.setText(text);
  state.checked = null;
  describe();
  check();
}

// -- a run, started from the page (US-44) ----------------------------------------------------------
// The names of the inputs the text declares, each with a value to start from: the quarter of the
// synthetic forms for "quarter", an empty string for the others. The person edits it before Start.
function inputsOf(text) {
  const block = /^inputs:[ \t]*\n((?:[ \t]+\S.*\n?)+)/m.exec(text);
  const guess = {};
  if (!block) return guess;
  const indent = /^([ \t]+)\S/.exec(block[1])[1];
  for (const found of block[1].matchAll(new RegExp(`^${indent}([A-Za-z_]\\w*):`, "gm"))) {
    guess[found[1]] = found[1] === "quarter" ? "2025Q1" : "";
  }
  return guess;
}

function openRunForm() {
  $("run-inputs").value = JSON.stringify(inputsOf(state.editor.getText()));
  $("run-form").hidden = false;
  $("run-inputs").focus();
}

async function startRun(event) {
  event.preventDefault();
  const c = state.current;
  if (!c) return;
  let inputs;
  try {
    inputs = JSON.parse($("run-inputs").value || "{}");
    if (inputs === null || typeof inputs !== "object" || Array.isArray(inputs)) throw new Error();
  } catch {
    say('the inputs must be a JSON object, for example {"quarter": "2025Q1"}');
    return;
  }
  try {
    const started = await call("POST", `/workflows/${encodeURIComponent(c.name)}/runs`, {
      inputs,
      version: c.number,
    });
    $("run-form").hidden = true;
    say(`run ${started.run_id} started`);
    goTo(`runs/${encodeURIComponent(started.run_id)}`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 403) {
      say("you may not start runs");
    } else {
      say(explain(error));
    }
  }
}


// -- addresses ---------------------------------------------------------------------------------
// A step to another page is a step in the history, so that the back button comes back to this one.
function goTo(path) {
  location.hash = `#/${path}`;
}

// The address that tells where the person is, put in place without a step in the history.
function keepAddress(hash) {
  if (location.hash !== hash) history.replaceState(null, "", hash);
}

// -- signing in --------------------------------------------------------------------------------
// A person signs in with a name and a password and gets a session token, or pastes an API token.
// Either way the token is kept for this tab only (api.js). When the core was started with
// --demo-logins it lists the demonstration users (and only then), and a button for each signs in
// as that user. A core that does not list them answers 404 and no button is shown.
const LABELS = {
  admin: "Admin",
  designer: "Designer",
  chief: "Chief statistician",
  operator: "Operator",
  operator2: "Second operator",
};

// Keeps the token and asks the core who it is. A token that is not accepted is forgotten.
async function enter(token) {
  session.set(token);
  try {
    state.user = await call("GET", "/me");
  } catch (error) {
    session.clear();
    state.user = null;
    throw error;
  }
  paintUser();
  state.checked = null;
}

function paintUser() {
  const user = state.user;
  const who = $("who");
  who.replaceChildren();
  if (user) {
    const name = document.createElement("strong");
    name.textContent = displayName(user.name);
    const role = document.createElement("span");
    role.className = "role";
    role.textContent = roleLabel(user);
    who.append(name, role);
  }
  for (const id of ["new", "example"]) $(id).hidden = !can("workflows:write");
  for (const link of document.querySelectorAll(".menu a")) {
    const needs = link.dataset.needs;
    link.hidden = !user || Boolean(needs && !user.permissions.includes(needs));
  }
}

function reason(error, tokenForm) {
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return tokenForm ? "This token is not accepted." : "The name or the password is wrong.";
    }
    if (error.status === 429) return "Too many attempts. Wait a minute and try again.";
    if (error.status === 0) return "The server cannot be reached.";
    return error.message;
  }
  return String(error);
}

// One sign-in attempt: ``attempt`` gets a user into the page or throws. On success the person goes
// where they wanted to go (the address that sent them here) or to the home page.
async function signIn(attempt, tokenForm = false) {
  const error = $("login-error");
  error.hidden = true;
  $("login-note").hidden = true;
  try {
    await attempt();
  } catch (failure) {
    error.hidden = false;
    error.textContent = reason(failure, tokenForm);
    return;
  }
  location.hash = nextOf(parse(location.hash));
}

async function signOut() {
  try {
    await call("POST", "/logout");
  } catch {
    /* the session ends at the core by itself; here it is forgotten in any case */
  }
  session.clear();
  location.hash = href("login");
  location.reload(); // nothing of the last person stays in the page
}

// A token that the core no longer accepts: the page asks for a new sign-in and, after it, comes
// back to the page that was open. The text in the editor stays, so that a draft is not lost.
function expired() {
  if (!state.user) return;
  session.clear();
  state.user = null;
  paintUser();
  const from = location.hash.slice(1);
  const query = from ? `next=${encodeURIComponent(from)}&ended` : "ended";
  history.replaceState(null, "", href("login", null, query));
  render();
}

async function loadLogins() {
  let found;
  try {
    // the route exists only on a demonstration, and /health says whether this is one (a request
    // for a route that is not there would put an error into the console of the browser)
    const health = await call("GET", "/health");
    if (health.demo_logins !== "yes") return;
    found = (await call("GET", "/demo/logins")).logins;
  } catch {
    return;
  }
  for (const login of found) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.login = login.name;
    button.textContent = LABELS[login.name] || login.name;
    button.addEventListener("click", () => signIn(() => enter(login.token)));
    $("logins-buttons").append(button);
  }
  $("logins").hidden = found.length === 0;
}

// -- pages ---------------------------------------------------------------------------------------
const TITLES = {
  home: "Home",
  editor: "Editor",
  forms: "Forms",
  runs: "Runs",
  services: "Services",
  migration: "Migration",
  statistics: "Statistics",
  login: "Sign in",
};

function showView(view) {
  for (const name of Object.keys(TITLES)) $(`view-${name}`).hidden = name !== view;
  for (const link of document.querySelectorAll(".menu a")) {
    if (link.dataset.view === view) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  $("top").hidden = view === "login";
  document.body.dataset.view = view;
  document.title = view === "login" ? "FlowStat" : `${TITLES[view]} · FlowStat`;
}

// Shows the page of the address. The sign-in page is the only one that is open to a person who is
// not signed in; any other address sends them there and is remembered, to be opened after.
async function render() {
  const route = parse(location.hash);
  if (route.view === "login") {
    if (state.user) {
      keepAddress(nextOf(route));
      return render();
    }
    state.view = "login";
    showView("login");
    $("login-note").hidden = !route.params.has("ended");
    $("login-name").focus();
    return undefined;
  }
  if (!state.user) {
    const from = route.known ? location.hash.slice(1) : "";
    keepAddress(href("login", null, from ? `next=${encodeURIComponent(from)}` : ""));
    return render();
  }
  if (!route.known) {
    keepAddress(href("home"));
    return render();
  }
  const same = state.view === route.view;
  state.view = route.view;
  showView(route.view);
  const view = route.view;
  if (view === "runs") {
    if (!same) {
      state.ops.loadRuns();
      state.ops.live.start();
    }
    if (route.arg) state.ops.openRun(route.arg);
  } else {
    state.ops.live.stop();
  }
  if (view === "services") {
    if (!same) {
      state.ops.loadServices();
      state.ops.liveServices.start();
    }
  } else {
    state.ops.liveServices.stop();
  }
  if (view === "home") state.home.load(state.user);
  if (view === "editor") enterEditor(route, same);
  if (view === "forms") state.forms.load(state.user);
  if (view === "migration" && !same) state.migration.load();
  if (view === "statistics" && !same) state.statistics.load();
  return undefined;
}

// The editor: a request in the address ("?example", "?new") is done once and taken out of it; a
// workflow in the address is opened.
function enterEditor(route, same) {
  if (!same) refresh();
  if (route.params.has("example") || route.params.has("new")) {
    fresh(route.params.has("example") ? EXAMPLE : SKELETON);
    keepAddress(href("editor"));
  } else if (route.arg && !(state.current && state.current.name === route.arg)) {
    open(route.arg);
  }
}

// -- start -----------------------------------------------------------------------------------
async function main() {
  state.ops = createOps({ goTo });
  state.migration = createMigration();
  state.statistics = createStatistics();
  state.home = createHome();
  state.forms = createForms();
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

  whenUnauthorized(expired);
  $("login-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const name = $("login-name").value.trim();
    const password = $("login-password").value;
    $("login-password").value = "";
    signIn(async () => {
      const made = await call("POST", "/login", { name, password });
      await enter(made.token);
    });
  });
  $("sign-in").addEventListener("submit", (event) => {
    event.preventDefault();
    const token = $("token").value.trim();
    $("token").value = "";
    signIn(() => enter(token), true);
  });
  $("sign-out").addEventListener("click", signOut);
  window.addEventListener("hashchange", render);
  loadLogins();
  $("new").addEventListener("click", () => fresh());
  $("example").addEventListener("click", () => fresh(EXAMPLE));
  $("run").addEventListener("click", openRunForm);
  $("run-cancel").addEventListener("click", () => ($("run-form").hidden = true));
  $("run-form").addEventListener("submit", startRun);
  $("preview").addEventListener("click", togglePreview);
  $("save").addEventListener("click", save);
  $("publish").addEventListener("click", publish);

  if (session.token) {
    try {
      state.user = await call("GET", "/me");
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) session.clear();
    }
  }
  paintUser();
  describe();
  await render();
  document.body.dataset.ready = state.editor.kind;
}

main();
