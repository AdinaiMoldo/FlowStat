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
    if (error.status === 401) return "sign in: paste a token and press Use";
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

export function createMigration() {
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

  async function check() {
    const rows = $("migration-rows");
    const status = $("migration-status");
    const name = $("migration-workflow").value;
    rows.replaceChildren();
    try {
      const query = `from=${$("migration-from").value}&to=${$("migration-to").value}`;
      const answer = await call("GET", `/workflows/${encodeURIComponent(name)}/migration?${query}`);
      const movable = answer.runs.filter((r) => r.migratable).length;
      status.textContent = `${answer.runs.length} unfinished run(s), ${movable} can move`;
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
  $("migration-check").addEventListener("click", check);
  return { load, check };
}
