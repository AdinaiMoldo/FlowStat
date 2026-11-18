// The home page: what the signed-in person can do, how much there is, and what waits for them.
// Everything that comes from the server is put into the page as text, never as markup.
import { call } from "./api.js";
import { href } from "./router.js";
import { kindOf } from "./ops.js";

const $ = (id) => document.getElementById(id);

const ROLES = {
  admin: "Administrator",
  designer: "Workflow designer",
  operator: "Operator",
};

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

function count(id, list) {
  $(id).textContent = list === null ? "–" : String(list.length);
}

function waitingItem(run) {
  const item = document.createElement("li");
  item.dataset.testid = "waiting";
  item.dataset.run = run.run_id;
  const text = document.createElement("span");
  text.textContent = `${run.run_id} · ${run.workflow} v${run.version}`;
  const link = document.createElement("a");
  link.className = "button primary small";
  link.href = href("runs", run.run_id);
  link.textContent = "Review";
  item.append(text, link);
  return item;
}

function recentRow(run) {
  const tr = document.createElement("tr");
  tr.dataset.testid = "recent";
  tr.dataset.run = run.run_id;
  const id = document.createElement("td");
  const link = document.createElement("a");
  link.href = href("runs", run.run_id);
  link.textContent = run.run_id;
  id.append(link);
  const flow = document.createElement("td");
  flow.textContent = `${run.workflow} v${run.version}`;
  const status = document.createElement("td");
  const word = run.outcome || run.status;
  const badge = document.createElement("span");
  badge.className = `badge ${kindOf(word)}`;
  badge.textContent = word;
  status.append(badge);
  tr.append(id, flow, status);
  return tr;
}

export function createHome() {
  // The buttons and tiles whose page the person may not use are hidden, as in the menu.
  function limit(user) {
    for (const element of $("view-home").querySelectorAll("[data-needs]")) {
      element.hidden = !user.permissions.includes(element.dataset.needs);
    }
  }

  async function load(user) {
    $("home-hello").textContent = `Welcome, ${user.name}`;
    $("home-role").textContent = roleLabel(user);
    limit(user);
    const [workflows, runs, services] = await Promise.all([
      listing(user, "workflows:read", "/workflows"),
      listing(user, "runs:read", "/runs"),
      listing(user, "deployments:read", "/deployments"),
    ]);
    count("count-workflows", workflows);
    count("count-runs", runs);
    count("count-services", services);
    const all = runs || [];
    const waiting = all.filter((r) => (r.outcome || r.status) === "WAITING");
    $("count-waiting").textContent = runs === null ? "–" : String(waiting.length);
    $("home-waiting").replaceChildren(...waiting.map(waitingItem));
    $("home-waiting-empty").hidden = runs === null || waiting.length > 0;
    const recent = [...all].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))).slice(0, 5);
    $("home-recent").replaceChildren(...recent.map(recentRow));
    $("home-recent-empty").hidden = runs === null || recent.length > 0;
  }

  return { load };
}
