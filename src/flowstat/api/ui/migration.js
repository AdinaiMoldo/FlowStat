// The view of migration: which unfinished runs of one version can move to another, and why not.
// Reasons come from the server and are put into the page as text, never as markup.
import { ApiError, call } from "./api.js";

const $ = (id) => document.getElementById(id);

function cell(text, kind) {
  const td = document.createElement("td");
  if (kind) {
    const badge = document.createElement("span");
    badge.className = `badge ${kind}`;
    badge.textContent = text;
    td.append(badge);
  } else {
    td.textContent = text;
  }
  return td;
}

function problem(error) {
  if (error instanceof ApiError) {
    if (error.status === 401) return "your session has ended: sign in again";
    if (error.status === 0) return "the server cannot be reached";
    return error.message;
  }
  return String(error);
}

function fill(select, values, label) {
  const keep = select.value;
  select.replaceChildren();
  for (const v of values) {
    const option = document.createElement("option");
    option.value = String(v);
    option.textContent = label(v);
    select.append(option);
  }
  if (values.map(String).includes(keep)) select.value = keep;
}

function reasonList(reasons) {
  const td = document.createElement("td");
  const list = document.createElement("ul");
  list.className = "reasons";
  for (const r of reasons) {
    const li = document.createElement("li");
    li.dataset.code = r.code;
    li.textContent = `${r.code}${r.step ? " " + r.step : ""}: ${r.message}`;
    list.append(li);
  }
  td.append(list);
  return td;
}

function mover(run, owners, onMove) {
  const td = document.createElement("td");
  if (!run.migratable) return td;
  const select = document.createElement("select");
  select.setAttribute("aria-label", `Where to move ${run.run_id}`);
  select.dataset.testid = "move-owner";
  for (const owner of owners) {
    const option = document.createElement("option");
    option.value = owner;
    option.textContent = owner === "core" ? "this core" : `service ${owner}`;
    select.append(option);
  }
  if (owners.includes(run.owner)) select.value = run.owner;
  const button = document.createElement("button");
  button.type = "button";
  button.dataset.testid = "move";
  button.textContent = "Move";
  button.addEventListener("click", () => onMove(run.run_id, select.value));
  td.append(select, button);
  return td;
}

export function createMigration() {
  async function owners(name, version) {
    const found = ["core"];
    try {
      const rows = await call("GET", "/deployments");
      for (const d of rows) {
        if (d.workflow === name && d.version === version && d.status === "running") found.push(d.id);
      }
    } catch (error) {
      // a person who may not list the services can still move runs inside the core
    }
    return found;
  }

  async function move(runId, owner) {
    const status = $("migration-status");
    const to = Number($("migration-to").value);
    let note = "";
    try {
      const done = await call("POST", `/runs/${encodeURIComponent(runId)}/migrate`, { to, owner });
      note = `${runId} moved to v${done.to} at ${done.owner === "core" ? "this core" : "service " + done.owner}`;
    } catch (error) {
      status.textContent = problem(error);
      return;
    }
    await check(note);
  }

  async function load() {
    const status = $("migration-status");
    try {
      const workflows = await call("GET", "/workflows");
      fill($("migration-workflow"), workflows.map((w) => w.name), (n) => n);
      status.textContent = workflows.length ? "" : "no workflows yet";
      await loadVersions();
    } catch (error) {
      status.textContent = problem(error);
    }
  }

  async function loadVersions() {
    const name = $("migration-workflow").value;
    if (!name) return;
    const versions = await call("GET", `/workflows/${encodeURIComponent(name)}/versions`);
    const published = versions.filter((v) => v.status === "published").map((v) => v.number);
    fill($("migration-from"), published, (n) => `v${n}`);
    fill($("migration-to"), published, (n) => `v${n}`);
    if (published.length > 1 && $("migration-from").value === $("migration-to").value) {
      $("migration-to").value = String(published[published.length - 1]);
      $("migration-from").value = String(published[published.length - 2]);
    }
  }

  async function check(note) {
    const rows = $("migration-rows");
    const status = $("migration-status");
    const name = $("migration-workflow").value;
    const targets = await owners(name, Number($("migration-to").value));
    rows.replaceChildren();
    try {
      const query = `from=${$("migration-from").value}&to=${$("migration-to").value}`;
      const answer = await call("GET", `/workflows/${encodeURIComponent(name)}/migration?${query}`);
      const movable = answer.runs.filter((r) => r.migratable).length;
      status.textContent = `${note ? note + "; " : ""}${answer.runs.length} unfinished run(s), ${movable} can move`;
      for (const r of answer.runs) {
        const tr = document.createElement("tr");
        tr.dataset.testid = "migration-run";
        tr.dataset.id = r.run_id;
        tr.append(
          cell(r.run_id),
          cell(r.owner),
          cell(r.status),
          cell(r.migratable ? "can move" : "cannot move", r.migratable ? "good" : "bad"),
          reasonList(r.reasons),
          cell(`${r.kept.length} kept, ${r.pending.length} to run${r.dropped.length ? ", " + r.dropped.length + " dropped" : ""}`),
          mover(r, targets, move),
        );
        rows.append(tr);
      }
    } catch (error) {
      status.textContent = problem(error);
    }
  }

  $("migration-workflow").addEventListener("change", () => loadVersions().catch((e) => {
    $("migration-status").textContent = problem(e);
  }));
  $("migration-check").addEventListener("click", () => check());
  return { load, check };
}
