// The view of reporting campaigns (US-53): the list, the state of one campaign (how many forms
// have come, how many are valid, what is missing), the changes a person may make now, and the
// form that makes or edits a draft. What the server sends is put into the page as text, never as
// markup. The rules of the life cycle are the server's: the page only offers what ``next`` says.
import { ApiError, call } from "./api.js";
import { day, deadlineWords, plural } from "./format.js";

const $ = (id) => document.getElementById(id);

const KINDS = {
  APPROVED: "good",
  FINALIZED: "good",
  REJECTED: "bad",
  PRELIMINARY: "wait",
  PENDING_APPROVAL: "wait",
  DRAFT: "off",
  ARCHIVED: "off",
};

const WORDS = {
  READY_FOR_CONSOLIDATION: "ready to consolidate",
  PENDING_APPROVAL: "waits for approval",
};

// What the button of a change says. A cancelled draft and an archived campaign both end in ARCHIVED.
function label(from, to) {
  if (from === "DRAFT" && to === "ARCHIVED") return "Cancel campaign";
  return (
    {
      OPEN: "Open for collecting",
      COLLECTING: from === "OPEN" ? "Start collecting" : "Collect again",
      VALIDATING: "Close collecting and validate",
      FINALIZED: "Finalize",
      ARCHIVED: "Archive",
    }[to] || to
  );
}

const ASKS_TWICE = new Set(["FINALIZED", "ARCHIVED"]);

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function statusBadge(status) {
  return element("span", `badge ${KINDS[status] || ""}`, WORDS[status] || status.toLowerCase().replaceAll("_", " "));
}

function problem(error) {
  if (error instanceof ApiError) {
    if (error.status === 401) return "your session has ended: sign in again";
    if (error.status === 0) return "the server cannot be reached";
    return error.message;
  }
  return String(error);
}

export function createCampaigns({ goTo }) {
  let user = null;
  let campaigns = [];
  let shown = null; // the id of the campaign in the right panel
  let editing = null; // the id of the draft being edited, or null for a new campaign
  let regionsCache = null;
  let pending = null; // the change that asked to be pressed twice
  let results = []; // the results of the campaign in the right panel, the newest first
  let openResult = null; // the number of the result whose figures are shown
  let runsOf = []; // the runs of the workflow of the campaign, the newest first

  const can = (permission) => user !== null && user.permissions.includes(permission);

  // -- the list ------------------------------------------------------------------------------
  function row(c) {
    const tr = element("tr");
    tr.dataset.testid = "campaign";
    tr.dataset.id = c.id;
    const name = element("td", "", c.name);
    const status = element("td");
    status.append(statusBadge(c.status));
    const forms = element("td", "r", `${c.progress.valid} of ${c.progress.expected}`);
    forms.title = `${c.progress.received} received, ${c.progress.valid} valid`;
    const due = element("td", "", day(c.deadline));
    due.title = c.deadline;
    tr.append(name, element("td", "", c.period), status, forms, due);
    tr.classList.toggle("current", c.id === shown);
    tr.addEventListener("click", () => goTo(`campaigns/${encodeURIComponent(c.id)}`));
    return tr;
  }

  function renderList() {
    $("campaigns-rows").replaceChildren(...campaigns.map(row));
    $("campaigns-table").hidden = campaigns.length === 0;
    $("campaigns-empty").hidden = campaigns.length > 0;
    $("campaigns-empty-action").textContent = can("campaigns:write")
      ? "Press New campaign to make the first one."
      : "An administrator makes the campaigns.";
    $("campaigns-status").textContent = campaigns.length ? plural(campaigns.length, "campaign") : "";
  }

  // -- one campaign --------------------------------------------------------------------------
  function counts(p) {
    const words = [`${p.received} of ${p.expected} received`, `${p.valid} valid`];
    if (p.invalid) words.push(`${p.invalid} with errors`);
    if (p.missing.length) words.push(`${p.missing.length} missing`);
    return words.join(", ");
  }

  function readiness(c) {
    const p = c.progress;
    const note = $("campaign-ready");
    note.hidden = false;
    note.classList.remove("good");
    if (p.final_ready) {
      note.classList.add("good");
      note.textContent = "Enough valid forms for the final result";
    } else if (p.preliminary_ready) {
      note.textContent = `Enough for a preliminary result. The final one needs ${c.final_min} valid forms (${p.valid} now).`;
    } else {
      note.textContent = `A preliminary result needs ${c.preliminary_min} valid forms (${p.valid} now).`;
    }
  }

  function regionRow(r) {
    const tr = element("tr");
    tr.dataset.testid = "campaign-region";
    tr.dataset.region = r.region;
    const state = element("td");
    let badge;
    if (!r.received) badge = element("span", "badge wait", "missing");
    else if (r.valid) badge = element("span", "badge good", r.warnings ? `valid, ${plural(r.warnings, "warning")}` : "valid");
    else badge = element("span", "badge bad", plural(r.problems, "error"));
    state.append(badge);
    tr.append(element("td", "", `${r.region} ${r.name}`), state);
    return tr;
  }

  function actions(c) {
    const box = $("campaign-actions");
    box.replaceChildren();
    if (!can("campaigns:write")) return;
    box.append(element("span", "spacer"));
    if (c.status === "DRAFT") {
      const edit = element("button", "", "Edit");
      edit.type = "button";
      edit.dataset.testid = "edit";
      edit.addEventListener("click", () => openForm(c));
      box.append(edit);
    }
    for (const to of c.next) {
      const button = element("button", to === "OPEN" ? "primary" : "", label(c.status, to));
      button.type = "button";
      button.dataset.testid = "move";
      button.dataset.to = to;
      button.addEventListener("click", () => change(c, to, button));
      box.append(button);
    }
  }

  function renderDetail() {
    const c = campaigns.find((x) => x.id === shown);
    $("campaign-choose").hidden = c !== undefined;
    $("campaign-body").hidden = c === undefined;
    $("campaign-title").textContent = c ? c.name : "Campaign";
    $("campaign-state").replaceChildren(...(c ? [statusBadge(c.status)] : []));
    if (!c) return;
    const words = deadlineWords(c.deadline);
    $("campaign-deadline-line").textContent = `Deadline ${day(c.deadline)}${words ? ` · ${words}` : ""}`;
    const p = c.progress;
    const bar = $("campaign-bar");
    bar.setAttribute("aria-valuenow", String(p.valid));
    bar.setAttribute("aria-valuemax", String(p.expected));
    bar.firstElementChild.style.width = `${p.expected ? (100 * p.valid) / p.expected : 0}%`;
    $("campaign-counts").textContent = counts(p);
    readiness(c);
    $("campaign-regions-rows").replaceChildren(...p.regions.map(regionRow));
    $("campaign-settings").textContent =
      `${c.workflow} v${c.workflow_version} · preliminary from ${c.preliminary_min}, final needs ${c.final_min} · approved by ${c.approver.replaceAll("_", " ")} · rules ${c.rules}`;
    $("campaign-note").hidden = true;
    actions(c);
    renderRuns(c);
    renderResults(c);
  }

  // -- the runs of the workflow (US-58) ------------------------------------------------------
  const RUNS_IN = new Set(["COLLECTING", "VALIDATING", "READY_FOR_CONSOLIDATION", "PRELIMINARY", "PENDING_APPROVAL", "APPROVED"]);

  function runRow(r) {
    const tr = element("tr");
    tr.dataset.testid = "campaign-run-row";
    tr.dataset.run = r.run_id;
    const open = element("button", "small", r.run_id);
    open.type = "button";
    open.title = "Open the run";
    open.addEventListener("click", () => goTo(`runs/${encodeURIComponent(r.run_id)}`));
    const id = element("td");
    id.append(open);
    const work = r.executed + r.reused === 0 ? "none yet" : `${r.executed} computed, ${r.reused} taken`;
    const cell = element("td", "", work);
    cell.title = "taken: the result of an earlier run was used, because the inputs of the step did not change";
    if (r.executed_steps.length) cell.title += `; computed: ${r.executed_steps.join(", ")}`;
    tr.append(
      id,
      element("td", "", r.outcome ? `${r.status} (${r.outcome})` : r.status),
      cell,
      element("td", "muted", `${r.started_by}, ${r.started_at.slice(0, 16).replace("T", " ")}`),
    );
    return tr;
  }

  function renderRuns(c) {
    $("campaign-run").hidden = !(can("runs:start") && RUNS_IN.has(c.status));
    $("campaign-run").onclick = () => startRun(c);
    $("campaign-runs-rows").replaceChildren(...runsOf.map(runRow));
    $("campaign-runs-table").hidden = runsOf.length === 0;
    $("campaign-runs-empty").hidden = runsOf.length > 0;
  }

  async function loadRuns(id) {
    try {
      runsOf = (await call("GET", `/campaigns/${encodeURIComponent(id)}/runs`)).runs;
    } catch {
      runsOf = [];
    }
  }

  async function startRun(c) {
    const note = $("campaign-runs-note");
    note.hidden = true;
    try {
      await call("POST", `/campaigns/${encodeURIComponent(c.id)}/runs`);
      await reload(c.id);
    } catch (error) {
      note.textContent = problem(error);
      note.hidden = false;
    }
  }

  // -- the results (US-57) -------------------------------------------------------------------
  const MAKES = new Set(["VALIDATING", "READY_FOR_CONSOLIDATION", "PRELIMINARY"]);

  function resultRow(c, r) {
    const tr = element("tr");
    tr.dataset.testid = "result";
    tr.dataset.number = String(r.number);
    const what = element("td");
    const open = element("button", "small", `#${r.number} ${r.kind === "FINAL" ? "final" : "preliminary"}`);
    open.type = "button";
    open.addEventListener("click", () => showResult(c, r.number));
    what.append(open, document.createTextNode(" "));
    what.append(element("span", r.complete ? "badge good" : "badge wait", r.complete ? "complete" : "incomplete"));
    if (r.stale) {
      const stale = element("span", "badge bad", "stale");
      stale.title = r.stale_reason || "";
      what.append(document.createTextNode(" "), stale);
    }
    tr.append(
      what,
      element("td", "", `${r.count} of ${r.expected} (${r.share}%)`),
      element("td", "muted", `${r.made_by}, ${r.made_at.slice(0, 16).replace("T", " ")}`),
    );
    return tr;
  }

  function renderResults(c) {
    const may = can("consolidations:write") && MAKES.has(c.status);
    $("result-preliminary").hidden = !may;
    $("result-final").hidden = !may;
    $("result-preliminary").onclick = () => makeResult(c, "PRELIMINARY");
    $("result-final").onclick = () => makeResult(c, "FINAL");
    $("results-rows").replaceChildren(...results.map((r) => resultRow(c, r)));
    $("results-table").hidden = results.length === 0;
    $("results-empty").hidden = results.length > 0;
    if (!results.some((r) => r.number === openResult)) {
      openResult = null;
      $("result-detail").hidden = true;
    }
  }

  async function loadResults(id) {
    try {
      results = (await call("GET", `/campaigns/${encodeURIComponent(id)}/consolidations`)).results;
    } catch {
      results = [];
    }
  }

  async function makeResult(c, kind) {
    const note = $("results-note");
    note.hidden = true;
    try {
      const made = await call("POST", `/campaigns/${encodeURIComponent(c.id)}/consolidations`, { kind });
      await reload(c.id);
      await showResult(c, made.number);
    } catch (error) {
      note.textContent = problem(error);
      note.hidden = false;
    }
  }

  async function showResult(c, number) {
    let r;
    try {
      r = await call("GET", `/campaigns/${encodeURIComponent(c.id)}/consolidations/${number}`);
    } catch (error) {
      $("results-note").textContent = problem(error);
      $("results-note").hidden = false;
      return;
    }
    openResult = number;
    const out = r.left_out.map((o) => `${o.region} (${o.reason}: ${o.detail})`);
    const stale = r.stale ? ` Stale: ${r.stale_reason}.` : "";
    $("result-left").textContent =
      `Result #${r.number}, ${r.kind === "FINAL" ? "final" : "preliminary"}, ${r.complete ? "complete" : "incomplete"}. ` +
      `In: ${r.included.map((i) => `${i.region} v${i.version}`).join(", ")}. ` +
      (out.length ? `Left out: ${out.join("; ")}.` : "No region is left out.") +
      stale;
    $("result-lines").replaceChildren(
      ...r.lines.map((line) => {
        const tr = element("tr");
        tr.dataset.testid = "result-line";
        tr.append(
          element("td", "", `${line.code} ${line.name}`),
          element("td", "", line.total),
          element("td", "muted", line.parts.map((p) => `${p.region} v${p.version}: ${p.value}`).join(" + ")),
        );
        return tr;
      }),
    );
    const words = { APPROVE: "Approved", REJECT: "Rejected", CORRECT: "A correction was asked for" };
    const taken = $("result-decision");
    taken.hidden = r.decision === null;
    if (r.decision) {
      taken.classList.toggle("good", r.decision.decision === "APPROVE");
      taken.textContent =
        `${words[r.decision.decision]} by ${r.decision.by}, ${r.decision.at.slice(0, 16).replace("T", " ")}` +
        (r.decision.comment ? `: ${r.decision.comment}` : "");
    }
    // the approver of the campaign decides about its current final result, once
    const holds = user.role === "admin" || user.titles.includes(c.approver);
    const open =
      can("consolidations:decide") && holds && r.decision === null && r.kind === "FINAL" && !r.stale && c.status === "PENDING_APPROVAL";
    $("result-decide").hidden = !open;
    if (open) {
      $("result-comment").value = "";
      for (const button of $("result-decide").querySelectorAll("[data-testid=decide]")) {
        button.onclick = () => decideResult(c, number, button.dataset.decision);
      }
    }
    $("result-detail").hidden = false;
  }

  async function decideResult(c, number, decision) {
    const note = $("results-note");
    note.hidden = true;
    try {
      await call("POST", `/campaigns/${encodeURIComponent(c.id)}/consolidations/${number}/decision`, {
        decision,
        comment: $("result-comment").value,
      });
    } catch (error) {
      note.textContent = problem(error);
      note.hidden = false;
      return;
    }
    await reload(c.id);
    const now = campaigns.find((x) => x.id === c.id);
    if (now) await showResult(now, number);
  }

  async function change(c, to, button) {
    const note = $("campaign-note");
    if (ASKS_TWICE.has(to) && pending !== `${c.id}:${to}`) {
      pending = `${c.id}:${to}`;
      button.textContent = `Press again to ${label(c.status, to).toLowerCase()}`;
      return;
    }
    pending = null;
    try {
      await call("POST", `/campaigns/${encodeURIComponent(c.id)}/transitions`, { to });
      await reload(c.id);
    } catch (error) {
      note.textContent = problem(error);
      note.hidden = false;
    }
  }

  // -- the form ------------------------------------------------------------------------------
  async function regionList() {
    if (regionsCache === null) {
      const found = await call("GET", "/forms?quarter=2025Q1");
      regionsCache = found.forms.map((f) => ({ code: f.region, name: f.name }));
    }
    return regionsCache;
  }

  async function openForm(c) {
    editing = c ? c.id : null;
    const note = $("campaign-form-note");
    note.hidden = true;
    $("campaign-form-title").textContent = c ? "Edit the draft" : "New campaign";
    const [workflows, regions] = await Promise.all([call("GET", "/workflows"), regionList()]);
    const select = $("campaign-workflow");
    select.replaceChildren();
    for (const w of workflows) {
      const number = w.latest_published ?? w.latest;
      const option = element("option", "", `${w.name} v${number}${w.latest_published === null ? " (draft)" : ""}`);
      option.value = JSON.stringify([w.name, number]);
      select.append(option);
    }
    const now = new Date();
    const quarter = c ? c.quarter : Math.floor(now.getMonth() / 3) + 1;
    const year = c ? c.year : now.getFullYear();
    const soon = new Date(now.getTime() + 30 * 86400000).toISOString().slice(0, 10);
    $("campaign-name").value = c ? c.name : `Quarterly Regional Statistics, Q${quarter} ${year}`;
    $("campaign-year").value = String(year);
    $("campaign-quarter").value = String(quarter);
    $("campaign-deadline").value = c ? c.deadline : soon;
    if (c) select.value = JSON.stringify([c.workflow, c.workflow_version]);
    $("campaign-preliminary").value = c ? String(c.preliminary_min) : "";
    $("campaign-final").value = c ? String(c.final_min) : "";
    $("campaign-rules").value = c ? c.rules : "regional-indicators-v1";
    const box = $("campaign-regions");
    box.replaceChildren();
    for (const r of regions) {
      const label_ = element("label", "check");
      const input = element("input");
      input.type = "checkbox";
      input.value = r.code;
      input.checked = c ? c.regions.includes(r.code) : true;
      label_.append(input, document.createTextNode(` ${r.code}`));
      label_.title = r.name;
      box.append(label_);
    }
    $("campaign-form").hidden = false;
    $("campaign-name").focus();
  }

  function closeForm() {
    $("campaign-form").hidden = true;
    editing = null;
  }

  async function save(event) {
    event.preventDefault();
    const note = $("campaign-form-note");
    const [workflow, version] = JSON.parse($("campaign-workflow").value || '["",0]');
    const number = (id) => ($(id).value === "" ? null : Number($(id).value));
    const body = {
      name: $("campaign-name").value,
      year: Number($("campaign-year").value),
      quarter: Number($("campaign-quarter").value),
      regions: [...$("campaign-regions").querySelectorAll("input:checked")].map((i) => i.value),
      deadline: $("campaign-deadline").value,
      workflow,
      workflow_version: version,
      preliminary_min: number("campaign-preliminary"),
      final_min: number("campaign-final"),
      rules: $("campaign-rules").value,
    };
    try {
      const saved = editing
        ? await call("PUT", `/campaigns/${encodeURIComponent(editing)}`, body)
        : await call("POST", "/campaigns", body);
      closeForm();
      await reload(saved.id);
      goTo(`campaigns/${encodeURIComponent(saved.id)}`);
    } catch (error) {
      note.textContent = problem(error);
      note.hidden = false;
    }
  }

  // -- loading -------------------------------------------------------------------------------
  async function reload(select) {
    try {
      campaigns = await call("GET", "/campaigns");
    } catch (error) {
      $("campaigns-status").textContent = problem(error);
      return;
    }
    if (select !== undefined) shown = select;
    if (shown && campaigns.some((x) => x.id === shown)) {
      await loadResults(shown);
      await loadRuns(shown);
    } else {
      results = [];
      runsOf = [];
    }
    renderList();
    renderDetail();
  }

  async function load(who, id) {
    user = who;
    for (const node of $("view-campaigns").querySelectorAll("[data-needs]")) {
      node.hidden = !can(node.dataset.needs);
    }
    if (!can("campaigns:write")) closeForm();
    await reload(id ?? shown);
  }

  $("campaigns-new").addEventListener("click", () => openForm(null));
  $("campaign-cancel").addEventListener("click", closeForm);
  $("campaign-form").addEventListener("submit", save);
  return { load };
}
