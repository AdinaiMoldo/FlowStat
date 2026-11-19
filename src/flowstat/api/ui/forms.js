// The page of the forms of the regions (US-50): which regions have sent their form for a quarter,
// and sending or removing one. What comes from the server or from a file name is put into the page
// as text, never as markup.
import { ApiError, call, upload } from "./api.js";

const $ = (id) => document.getElementById(id);
const QUARTER = /^20[0-9]{2}Q[1-4]$/;

function problem(error) {
  if (error instanceof ApiError) {
    if (error.status === 401) return "your session has ended: sign in again";
    if (error.status === 403 && error.message.includes("E-AUTH-005")) return error.message.replace(/^E-[A-Z]+-\d+ /, "");
    if (error.status === 403) return "you may not send forms (an operator or an admin may)";
    if (error.status === 0) return "the server cannot be reached";
    return error.message.replace(/^E-[A-Z]+-\d+ /, "");
  }
  return String(error);
}

function cell(text) {
  const td = document.createElement("td");
  td.textContent = text;
  return td;
}

function badge(text, kind) {
  const span = document.createElement("span");
  span.className = `badge ${kind}`;
  span.textContent = text;
  return span;
}

// One finding of the content rules: the message, its code, and what was found and expected.
function finding(f) {
  const item = document.createElement("li");
  item.dataset.severity = f.severity;
  item.dataset.code = f.code;
  item.append(document.createTextNode(`${f.message} `));
  const tag = document.createElement("span");
  tag.className = `muted code ${f.severity}`;
  tag.textContent = f.code;
  tag.title = [f.actual && `found ${f.actual}`, f.expected && `expected ${f.expected}`, f.sheet && `sheet ${f.sheet}`]
    .filter(Boolean)
    .join(", ");
  item.append(tag);
  return item;
}

// A time of the core's clock as people read it, in UTC (the page does not guess a time zone).
function when(iso) {
  return iso ? iso.slice(0, 16).replace("T", " ") : "";
}

function names(list) {
  return list.map((f) => `${f.code} ${f.indicator || f.cell}`).join(", ");
}

export function createForms() {
  const state = { forms: [], canWrite: false, offices: [], quarter: "2025Q1", open: new Set() };

  const quarter = () => $("forms-quarter").value.trim().toUpperCase();

  function say(text) {
    $("forms-status").textContent = text;
  }

  function summary() {
    const got = state.forms.filter((f) => f.received).length;
    const total = state.forms.length;
    const broken = state.forms.filter((f) => f.problems.length > 0).map((f) => f.region);
    const bad = broken.length;
    $("forms-count").textContent = `${got} of ${total} regions have sent their form`;
    $("forms-clear").hidden = !state.canWrite || got === 0;
    const note = $("forms-note");
    note.hidden = total === 0;
    if (total === 0) return;
    const parts = [];
    if (bad > 0) parts.push(`${broken.join(", ")} ${bad === 1 ? "breaks" : "break"} the rules: the run will stop at the check of ${bad === 1 ? "that form" : "those forms"}.`);
    if (got < 7) parts.push(`The consolidation needs at least 7 of the ${total} forms; ${got} came.`);
    else if (bad === 0) parts.push("Enough forms came and all of them pass the rules: the run can be started.");
    note.textContent = parts.join(" ");
    note.classList.toggle("good", got >= 7 && bad === 0);
  }

  function line(form) {
    const tr = document.createElement("tr");
    tr.dataset.testid = "form";
    tr.dataset.region = form.region;
    const where = cell("");
    const code = document.createElement("strong");
    code.textContent = form.region;
    where.append(code, document.createTextNode(` ${form.name}`));
    const status = cell("");
    status.append(badge(form.received ? "received" : "missing", form.received ? "good" : "wait"));
    if (form.version !== null && form.version !== undefined) {
      const tag = document.createElement("span");
      tag.className = "muted";
      tag.dataset.testid = "version";
      tag.textContent = ` v${form.version}`;
      tag.title = `sent by ${form.uploaded_by} at ${when(form.uploaded_at)} UTC`;
      status.append(tag);
    }
    const rows = cell(form.rows === null ? "" : String(form.rows));
    const size = cell(form.size === null ? "" : `${form.size} B`);
    const rules = cell("");
    rules.dataset.testid = "rules";
    if (!form.received) {
      rules.textContent = "";
    } else {
      const notes = (form.findings || []).filter((f) => f.severity !== "error");
      if (form.problems.length === 0) {
        const count = notes.filter((f) => f.severity === "warning").length;
        rules.append(badge(count ? `passes the rules, ${count} ${count === 1 ? "warning" : "warnings"}` : "passes the rules", "good"));
      } else {
        rules.append(badge("breaks the rules", "bad"));
      }
      const list = document.createElement("ul");
      list.className = "reasons";
      const errors = (form.findings || []).filter((f) => f.severity === "error");
      // what blocks the form first, the cut list when the file could not be read at all
      for (const f of errors.length ? errors.slice(0, 5) : []) list.append(finding(f));
      if (errors.length === 0) {
        for (const text of form.problems) {
          const item = document.createElement("li");
          item.textContent = text;
          list.append(item);
        }
      } else if (errors.length > 5) {
        const item = document.createElement("li");
        item.textContent = `and ${errors.length - 5} more`;
        list.append(item);
      }
      for (const f of notes) list.append(finding(f));
      if (list.children.length) rules.append(list);
    }
    const actions = cell("");
    // An operator of regional offices changes only their forms (the server refuses the others).
    const mine = state.offices.length === 0 || state.offices.includes(form.region);
    if (state.canWrite && !mine) {
      const note = document.createElement("span");
      note.className = "muted";
      note.textContent = "another office";
      actions.append(note);
    }
    if (state.canWrite && mine) {
      const choose = document.createElement("label");
      choose.className = "button small";
      choose.textContent = form.received ? "Replace" : "Choose file";
      const input = document.createElement("input");
      input.type = "file";
      input.accept = ".xlsx";
      input.className = "file";
      input.dataset.region = form.region;
      input.addEventListener("change", () => sendFiles([[form.region, input.files[0]]]));
      choose.append(input);
      actions.append(choose);
      if (form.received) {
        const drop = document.createElement("button");
        drop.type = "button";
        drop.className = "small";
        drop.textContent = "Remove";
        drop.addEventListener("click", () => remove(form.region));
        actions.append(drop);
      }
    }
    if (form.versions > 0) {
      const open = document.createElement("button");
      open.type = "button";
      open.className = "small";
      open.dataset.testid = "history-toggle";
      open.textContent = state.open.has(form.region) ? "Hide history" : `History (${form.versions})`;
      open.addEventListener("click", () => {
        if (state.open.has(form.region)) state.open.delete(form.region);
        else state.open.add(form.region);
        paint();
      });
      actions.append(open);
    }
    tr.append(where, status, rows, size, rules, actions);
    const out = [tr];
    if (form.versions > 0 && state.open.has(form.region)) out.push(historyRow(form));
    return out;
  }

  // The versions of one form and what the newest one changed. It is read when the row is opened.
  function historyRow(form) {
    const tr = document.createElement("tr");
    tr.dataset.testid = "history";
    tr.dataset.region = form.region;
    const td = document.createElement("td");
    td.colSpan = 6;
    td.className = "muted";
    td.textContent = "loading";
    tr.append(td);
    const base = `/forms/${encodeURIComponent(form.region)}/${encodeURIComponent(quarter())}`;
    (async () => {
      try {
        const { versions } = await call("GET", `${base}/versions`);
        const box = document.createElement("div");
        const list = document.createElement("ul");
        list.className = "reasons history";
        for (const v of versions) {
          const item = document.createElement("li");
          item.dataset.version = String(v.version);
          const state_ = v.removed_at ? `removed by ${v.removed_by} at ${when(v.removed_at)}` : v.current ? "current" : "replaced";
          const counts = v.errors ? `${v.errors} ${v.errors === 1 ? "error" : "errors"}` : "no errors";
          item.textContent = `v${v.version} · ${state_} · sent by ${v.uploaded_by} at ${when(v.uploaded_at)} · ${counts}${v.warnings ? `, ${v.warnings} ${v.warnings === 1 ? "warning" : "warnings"}` : ""}`;
          list.append(item);
        }
        box.append(list);
        if (versions.length >= 2) {
          const c = await call("GET", `${base}/compare`);
          const line_ = document.createElement("p");
          line_.dataset.testid = "comparison";
          const parts = [`Compared with v${c.older}:`];
          parts.push(c.fixed.length ? `fixed ${c.fixed.length} (${names(c.fixed)})` : "nothing fixed");
          parts.push(c.remaining.length ? `${c.remaining.length} still there (${names(c.remaining)})` : "none still there");
          parts.push(c.introduced.length ? `${c.introduced.length} new (${names(c.introduced)})` : "none new");
          line_.textContent = parts.join("; ") + ".";
          box.append(line_);
        }
        td.className = "";
        td.replaceChildren(box);
      } catch (error) {
        td.textContent = problem(error);
      }
    })();
    return tr;
  }

  function paint() {
    $("forms-rows").replaceChildren(...state.forms.flatMap(line));
  }

  async function load(user) {
    state.canWrite = user.permissions.includes("forms:write");
    state.offices = user.regions || [];
    $("forms-send").hidden = !state.canWrite;
    $("forms-readonly").hidden = state.canWrite;
    state.quarter = quarter() || state.quarter;
    await show();
  }

  async function show() {
    const q = quarter();
    if (!QUARTER.test(q)) {
      say("the quarter must look like 2025Q1");
      return;
    }
    try {
      const result = await call("GET", `/forms?quarter=${encodeURIComponent(q)}`);
      state.forms = result.forms;
    } catch (error) {
      say(problem(error));
      return;
    }
    say("");
    paint();
    summary();
  }

  // Several files at once: a file is for the region its name says (BIS.xlsx is for BIS).
  function regionOf(file) {
    const code = file.name.replace(/\.xlsx$/i, "").toUpperCase();
    return state.forms.some((f) => f.region === code) ? code : null;
  }

  async function sendMany(files) {
    const matched = [];
    const left = [];
    for (const file of files) {
      const region = regionOf(file);
      if (region) matched.push([region, file]);
      else left.push(file.name);
    }
    const failed = await sendFiles(matched, left.length ? `no region for: ${left.join(", ")}` : "");
    return failed;
  }

  async function sendFiles(pairs, extra = "") {
    const q = quarter();
    if (!QUARTER.test(q)) {
      say("the quarter must look like 2025Q1");
      return;
    }
    const failures = [];
    let sent = 0;
    for (const [region, file] of pairs) {
      if (!file) continue;
      try {
        await upload(`/forms/${encodeURIComponent(region)}/${encodeURIComponent(q)}`, file);
        sent += 1;
      } catch (error) {
        failures.push(`${file.name}: ${problem(error)}`);
      }
    }
    await show();
    const lines = [sent ? `${sent} form(s) sent` : "", failures.length ? `${failures.length} not accepted` : "", extra].filter(Boolean);
    say(lines.join(". "));
    $("forms-result").replaceChildren(
      ...failures.map((text) => {
        const item = document.createElement("li");
        item.dataset.testid = "form-error";
        item.textContent = text;
        return item;
      }),
    );
  }

  async function remove(region) {
    try {
      await call("DELETE", `/forms/${encodeURIComponent(region)}/${encodeURIComponent(quarter())}`);
    } catch (error) {
      say(problem(error));
      return;
    }
    await show();
    say(`the form of ${region} was removed`);
  }

  // Every form of the quarter, to begin again (the forms of an earlier try stay until they are removed).
  async function clear() {
    const q = quarter();
    const regions = state.forms.filter((f) => f.received).map((f) => f.region);
    try {
      for (const region of regions) {
        await call("DELETE", `/forms/${encodeURIComponent(region)}/${encodeURIComponent(q)}`);
      }
    } catch (error) {
      say(problem(error));
      await show();
      return;
    }
    await show();
    say(`${regions.length} form(s) of ${q} removed`);
  }

  $("forms-clear").addEventListener("click", clear);
  $("forms-refresh").addEventListener("click", show);
  $("forms-quarter").addEventListener("change", show);
  $("forms-files").addEventListener("change", (event) => {
    sendMany([...event.target.files]);
    event.target.value = "";
  });
  const zone = $("forms-drop");
  zone.addEventListener("dragover", (event) => {
    event.preventDefault();
    zone.classList.add("over");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("over");
    sendMany([...event.dataTransfer.files]);
  });

  return { load };
}
