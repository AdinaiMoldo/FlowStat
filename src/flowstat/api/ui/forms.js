// The page of the forms of the regions (US-50): which regions have sent their form for a quarter,
// and sending or removing one. What comes from the server or from a file name is put into the page
// as text, never as markup.
import { ApiError, call, upload } from "./api.js";

const $ = (id) => document.getElementById(id);
const QUARTER = /^20[0-9]{2}Q[1-4]$/;

function problem(error) {
  if (error instanceof ApiError) {
    if (error.status === 401) return "your session has ended: sign in again";
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

export function createForms() {
  const state = { forms: [], canWrite: false, quarter: "2025Q1" };

  const quarter = () => $("forms-quarter").value.trim().toUpperCase();

  function say(text) {
    $("forms-status").textContent = text;
  }

  function summary() {
    const got = state.forms.filter((f) => f.received).length;
    const total = state.forms.length;
    const bad = state.forms.filter((f) => f.problems.length > 0).length;
    $("forms-count").textContent = `${got} of ${total} regions have sent their form`;
    const note = $("forms-note");
    note.hidden = total === 0;
    if (total === 0) return;
    const parts = [];
    if (bad > 0) parts.push(`${bad} form(s) break the rules: the run will stop at their check.`);
    if (got < 7) parts.push("The consolidation needs at least 7 of the 9 forms.");
    else if (bad === 0) parts.push("Enough forms came: the run can be started.");
    note.textContent = parts.join(" ");
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
    const rows = cell(form.rows === null ? "" : String(form.rows));
    const size = cell(form.size === null ? "" : `${form.size} B`);
    const rules = cell("");
    rules.dataset.testid = "rules";
    if (!form.received) {
      rules.textContent = "";
    } else if (form.problems.length === 0) {
      rules.append(badge("passes the rules", "good"));
    } else {
      const list = document.createElement("ul");
      list.className = "reasons";
      for (const text of form.problems) {
        const item = document.createElement("li");
        item.textContent = text;
        list.append(item);
      }
      rules.append(badge("breaks the rules", "bad"), list);
    }
    const actions = cell("");
    if (state.canWrite) {
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
    tr.append(where, status, rows, size, rules, actions);
    return tr;
  }

  async function load(user) {
    state.canWrite = user.permissions.includes("forms:write");
    $("forms-send").hidden = !state.canWrite;
    state.quarter = quarter() || state.quarter;
    await show();
  }

  async function show() {
    const q = quarter();
    const rows = $("forms-rows");
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
    rows.replaceChildren(...state.forms.map(line));
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
    const lines = [sent ? `${sent} form(s) sent` : "", ...failures, extra].filter(Boolean);
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
