// The home page: a desk for the signed-in person. What is on it depends on the role: a designer sees
// the workflows, an operator the forms of the quarter, the chief statistician (and an admin) the
// runs that wait for an answer. Everything that comes from the server is put into the page as
// text, never as markup.
import { ApiError, call } from "./api.js";
import { copyable, greeting, plural, shortId, when } from "./format.js";
import { kindOf } from "./ops.js";
import { href } from "./router.js";

const $ = (id) => document.getElementById(id);

const ROLES = {
  admin: "Administrator",
  designer: "Workflow designer",
  operator: "Operator",
};

const WEEK_MS = 7 * 24 * 3600 * 1000;
const QUARTER = "2025Q1"; // the quarter that the page Forms opens on

// The names of users are lower case in the core; a person is greeted by the name with a capital.
export function displayName(name) {
  return name.charAt(0).toUpperCase() + name.slice(1);
}

export function roleLabel(user) {
  const base = ROLES[user.role] || user.role;
  const titles = user.titles.map((t) => t.replaceAll("_", " "));
  return titles.length ? `${base}, ${titles.join(", ")}` : base;
}

// A listing that the person may not read, or that cannot be read now, is null: the number shows a dash.
async function listing(user, permission, path) {
  if (!user.permissions.includes(permission)) return null;
  try {
    return await call("GET", path);
  } catch {
    return null;
  }
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function badge(word) {
  return element("span", `badge ${kindOf(word)}`, word);
}

function link(text, route, className = "button small") {
  const a = element("a", className, text);
  a.href = route;
  return a;
}

function tile(number, hint, ids) {
  $(ids.count).textContent = number === null ? "–" : String(number);
  $(ids.hint).textContent = hint;
}

// -- the runs ----------------------------------------------------------------------------------
function waitingItem(run, answer) {
  const item = element("li");
  item.dataset.testid = "waiting";
  item.dataset.run = run.run_id;
  const text = element("span", "grow");
  text.append(
    copyable(element("code", "", shortId(run.run_id)), run.run_id),
    element("span", "muted sub", `${run.workflow} v${run.version} · started ${when(run.created_at)}`),
  );
  item.append(text, link("Review", href("runs", run.run_id), "button small"));
  if (answer) {
    const approve = element("button", "primary small", "Approve");
    approve.type = "button";
    approve.dataset.testid = "approve";
    approve.addEventListener("click", () => answer(run, approve));
    item.append(approve);
  }
  return item;
}

function recentRow(run) {
  const tr = element("tr");
  tr.dataset.testid = "recent";
  tr.dataset.run = run.run_id;
  const id = element("td");
  const anchor = link(shortId(run.run_id), href("runs", run.run_id), "mono");
  anchor.title = run.run_id;
  id.append(anchor);
  const status = element("td");
  status.append(badge(run.outcome || run.status));
  const started = element("td", "muted", when(run.created_at));
  started.title = run.created_at;
  tr.append(id, element("td", "", `${run.workflow} v${run.version}`), status, started);
  return tr;
}

// -- the panels ----------------------------------------------------------------------------------
function waitingPanel(waiting, answer) {
  const body = element("div");
  if (waiting.length === 0) {
    body.append(element("p", "muted", "Nothing is waiting for you."));
    return body;
  }
  const list = element("ul", "items");
  list.id = "home-waiting";
  list.append(...waiting.map((run) => waitingItem(run, answer)));
  body.append(list);
  return body;
}

function workflowsPanel(workflows, runs) {
  const body = element("div");
  if (workflows === null || workflows.length === 0) {
    const empty = element("p", "muted", "No workflows yet. ");
    empty.append(link("Load the example", href("editor", null, "example"), ""));
    body.append(empty);
    return body;
  }
  const table = element("table", "table static");
  const head = element("thead");
  const row = element("tr");
  for (const [text, cls] of [["Workflow", ""], ["Version", ""], ["Status", ""], ["Runs", "r"]]) {
    row.append(element("th", cls, text));
  }
  head.append(row);
  const rows = element("tbody");
  for (const w of workflows) {
    const tr = element("tr");
    tr.dataset.testid = "workflow";
    tr.dataset.name = w.name;
    const name = element("td");
    name.append(link(w.name, href("editor", w.name), ""));
    const published = w.latest_published === w.latest;
    const status = element("td");
    status.append(badge(published ? "published" : "draft"));
    const mine = (runs || []).filter((r) => r.workflow === w.name).length;
    tr.append(name, element("td", "", `v${w.latest}`), status, element("td", "r", String(mine)));
    rows.append(tr);
  }
  table.append(head, rows);
  body.append(table);
  return body;
}

function formsPanel(forms) {
  const body = element("div");
  if (forms === null) return body;
  const total = forms.forms.length;
  const got = forms.forms.filter((f) => f.received).length;
  const bad = forms.forms.filter((f) => f.problems.length > 0).length;
  const bar = element("div", "progress");
  bar.setAttribute("role", "progressbar");
  bar.setAttribute("aria-valuenow", String(got));
  bar.setAttribute("aria-valuemax", String(total));
  const fill = element("span");
  fill.style.width = `${total ? (100 * got) / total : 0}%`;
  bar.append(fill);
  const words = [`${got} of ${total} regions sent`];
  if (bad > 0) words.push(`${plural(bad, "form")} ${bad === 1 ? "breaks" : "break"} the rules`);
  const text = element("p", "", words.join(", "));
  text.dataset.testid = "forms-progress";
  body.append(bar, text, link("Send the forms", href("forms"), "button primary small"));
  return body;
}

export function createHome() {
  let current = null;

  // The buttons and tiles whose page the person may not use are hidden, as in the menu.
  function limit(user) {
    for (const node of $("view-home").querySelectorAll("[data-needs]")) {
      node.hidden = !user.permissions.includes(node.dataset.needs);
    }
  }

  // Approve from the list: the step that waits is read from the run, then answered.
  async function answer(run, button) {
    button.disabled = true;
    try {
      const report = await call("GET", `/runs/${encodeURIComponent(run.run_id)}`);
      const step = report.steps.find((s) => s.state === "WAITING");
      if (!step) throw new ApiError(409, [{ code: "E-HOME", message: "nothing waits in this run now" }]);
      await call("POST", `/runs/${encodeURIComponent(run.run_id)}/decisions`, {
        step: step.step,
        decision: "approved",
      });
      $("home-main-note").textContent = `${shortId(run.run_id)} approved`;
      setTimeout(() => current && load(current, $("home-main-note").textContent), 900);
    } catch (error) {
      button.disabled = false;
      $("home-main-note").textContent =
        error instanceof ApiError && error.status === 403
          ? "Only the chief statistician may approve"
          : error.message;
    }
  }

  async function load(user, note = "") {
    current = user;
    $("home-hello").textContent = `${greeting()}, ${displayName(user.name)}`;
    $("home-role").textContent = roleLabel(user);
    limit(user);
    const wantsForms = user.role === "operator" && !user.titles.includes("chief_statistician");
    const [workflows, runs, services, forms] = await Promise.all([
      listing(user, "workflows:read", "/workflows"),
      listing(user, "runs:read", "/runs"),
      listing(user, "deployments:read", "/deployments"),
      wantsForms ? listing(user, "forms:read", `/forms?quarter=${QUARTER}`) : null,
    ]);
    const now = Date.now();
    const all = runs || [];
    const week = all.filter((r) => now - new Date(r.created_at).getTime() < WEEK_MS);
    const failedWeek = week.filter((r) => ["FAILED", "COMPENSATED", "COMPENSATION_FAILED"].includes(r.outcome));
    const waiting = all
      .filter((r) => (r.outcome || r.status) === "WAITING")
      .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
    tile(workflows === null ? null : workflows.length, workflows === null ? "" : `${workflows.filter((w) => w.latest_published !== null).length} published`, {
      count: "count-workflows", hint: "hint-workflows",
    });
    tile(runs === null ? null : all.length, runs === null ? "" : `${week.length} this week, ${failedWeek.length} failed`, {
      count: "count-runs", hint: "hint-runs",
    });
    tile(runs === null ? null : waiting.length, waiting.length ? `oldest ${when(waiting[0].created_at)}` : "all answered", {
      count: "count-waiting", hint: "hint-waiting",
    });
    const running = (services || []).filter((s) => s.status === "running").length;
    tile(services === null ? null : services.length, services === null ? "" : `${running} running`, {
      count: "count-services", hint: "hint-services",
    });

    // the panel of the role
    const body = $("home-main-body");
    $("home-main-note").textContent = note;
    if (user.role === "designer") {
      $("home-main-title").textContent = "Your workflows";
      body.replaceChildren(workflowsPanel(workflows, runs));
    } else if (wantsForms) {
      $("home-main-title").textContent = `Forms for ${QUARTER}`;
      body.replaceChildren(formsPanel(forms));
    } else {
      $("home-main-title").textContent = "Waiting for your approval";
      body.replaceChildren(waitingPanel(waiting, answer));
    }

    const recent = [...all]
      .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
      .slice(0, 8);
    $("home-recent").replaceChildren(...recent.map(recentRow));
    $("home-recent-empty").hidden = runs === null || recent.length > 0;
  }

  return { load };
}
