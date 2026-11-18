// The views of operations: the runs the core knows (its own and those shipped by services) and
// the services that were deployed. Everything that comes from a service (a message, an input) is
// put into the page as text, never as markup.
import { ApiError, call } from "./api.js";
import { createGraph } from "./graph.js";
import { createLive } from "./live.js";

const $ = (id) => document.getElementById(id);

const GOOD = new Set(["SUCCEEDED", "FINISHED", "RUNNING", "running"]);
const BAD = new Set([
  "FAILED", "GOAL_NOT_REACHED", "failed", "unreachable", "COMPENSATED", "COMPENSATION_FAILED",
]);

// Steps that did not run, and why: a cancelled run, a step whose input never came.
const OFF = new Set(["CANCELLED", "UPSTREAM_FAILED", "SKIPPED", "PENDING"]);

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

export function kindOf(word) {
  if (GOOD.has(word)) return "good";
  if (BAD.has(word)) return "bad";
  if (OFF.has(word)) return "off";
  return word === "WAITING" || word === "starting" ? "wait" : "";
}

function row(testid, id, cells) {
  const tr = document.createElement("tr");
  tr.dataset.testid = testid;
  tr.dataset.id = id;
  tr.append(...cells);
  return tr;
}

function problem(error) {
  if (error instanceof ApiError) {
    if (error.status === 401) return "your session has ended: sign in again";
    if (error.status === 0) return "the server cannot be reached";
    return error.message;
  }
  return String(error);
}

// A change of a run is shown within five seconds: one period, and the time of one answer.
const DEFAULT_LIVE_MS = 4000;

function livePeriod() {
  const asked = Number(new URLSearchParams(location.search).get("live"));
  return Number.isFinite(asked) && asked > 0 ? asked : DEFAULT_LIVE_MS;
}

export function createOps({ goTo }) {
  const state = {
    from: "",
    current: null,
    services: [],
    origins: new Map(), // run id -> the service that shipped it, or null for a run of the core
    graphKey: null, // the version whose graph is drawn in the run view
    runGraph: null,
  };

  // The table of services. ``quiet`` is a refresh the person did not ask for: when the server
  // cannot be asked the table stays as it is. Returns false when asking again is pointless.
  async function loadServices(quiet = false) {
    const rows = $("services-rows");
    try {
      state.services = await call("GET", "/deployments");
      $("services-status").textContent = state.services.length ? `${state.services.length} service(s)` : "";
    } catch (error) {
      if (!quiet) {
        state.services = [];
        rows.replaceChildren();
      }
      $("services-status").textContent = problem(error);
      return !(error instanceof ApiError && error.status === 401);
    }
    rows.replaceChildren();
    $("services-empty").hidden = state.services.length > 0;
    $("services-table").hidden = state.services.length === 0;
    $("services-hint").hidden = state.services.length === 0;
    for (const s of state.services) {
      const tr = row("service", s.id, [
        cell(s.id),
        cell(`${s.workflow} v${s.version}`),
        cell(s.kind),
        cell(s.status, kindOf(s.status)),
        cell(s.endpoint || ""),
        cell(String(s.runs)),
        cell(s.last_event_at || "none yet"),
      ]);
      tr.title = s.detail || "";
      tr.addEventListener("click", () => {
        state.from = s.id;
        goTo("runs");
      });
      rows.append(tr);
    }
    return true;
  }

  function fillSources() {
    const select = $("runs-from");
    select.replaceChildren();
    const add = (value, label) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      select.append(option);
    };
    add("", "everywhere");
    add("core", "this core");
    for (const s of state.services) add(s.id, `service ${s.id}`);
    select.value = state.from;
    if (select.value !== state.from) state.from = select.value;
  }

  async function loadRuns() {
    await loadServices();
    fillSources();
    await showRuns(false);
  }

  // The table of runs. A refresh that is not asked for by the person (``quiet``) leaves the table
  // as it is when the server cannot be asked, and says so in the status; it returns false when
  // asking again is pointless (not signed in).
  async function showRuns(quiet) {
    const rows = $("runs-rows");
    // "this core" has no query of its own: the listing is filtered here
    const query = state.from && state.from !== "core" ? `?deployment=${encodeURIComponent(state.from)}` : "";
    try {
      let runs = await call("GET", `/runs${query}`);
      if (state.from === "core") runs = runs.filter((r) => r.origin === null);
      $("runs-status").textContent = `${runs.length} run(s)`;
      rows.replaceChildren();
      for (const r of runs) {
        state.origins.set(r.run_id, r.origin);
        const status = r.outcome || r.status;
        const tr = row("run", r.run_id, [
          cell(r.run_id),
          cell(`${r.workflow} v${r.version}`),
          cell(status, kindOf(status)),
          cell(r.origin ? `service ${r.origin}` : "core"),
          cell(r.created_at),
        ]);
        if (state.current === r.run_id) tr.classList.add("current");
        tr.addEventListener("click", () => goTo(`runs/${encodeURIComponent(r.run_id)}`));
        rows.append(tr);
      }
      return true;
    } catch (error) {
      if (!quiet) rows.replaceChildren();
      $("runs-status").textContent = problem(error);
      return !(error instanceof ApiError && error.status === 401);
    }
  }

  // What the page asks for by itself while the runs are on the screen: the table and the report
  // of the run that is open (its steps change as it goes).
  async function refresh() {
    const more = await showRuns(true);
    if (more && state.current) await openRun(state.current, true);
    return more;
  }

  const live = createLive({ work: refresh, every: livePeriod() });
  $("runs-status").dataset.liveMs = String(live.every);
  const liveServices = createLive({ work: () => loadServices(true), every: livePeriod() });
  $("services-status").dataset.liveMs = String(liveServices.every);

  // The graph of the version of the run, coloured by the states of its steps (US-44). The graph is
  // fetched once for a version; a refresh of the run only changes the colours.
  async function showGraph(report) {
    const pane = $("run-graph-pane");
    const key = `${report.workflow}@${report.version}`;
    try {
      if (state.graphKey !== key) {
        const path = `/workflows/${encodeURIComponent(report.workflow)}/versions/${report.version}/graph`;
        const result = await call("GET", path);
        pane.hidden = false; // the box must be on the screen when the drawing is made
        state.runGraph ||= createGraph($("run-graph"), () => {}, { collapseOver: 1 });
        state.runGraph.draw(result.graph);
        state.graphKey = key;
      }
      pane.hidden = false;
      state.runGraph.paint(new Map(report.steps.map((s) => [s.step, s.state])));
    } catch {
      pane.hidden = true; // no graph (a token that may not read it, a version that is gone)
      state.graphKey = null;
    }
  }

  function say(text) {
    const note = $("run-note");
    note.hidden = !text;
    note.textContent = text;
  }

  // The answer of a person to an approval that waits. The core checks the title; a refusal is
  // shown as the page of the run, not as an error of the page.
  async function decide(runId, step, decision) {
    try {
      await call("POST", `/runs/${encodeURIComponent(runId)}/decisions`, { step, decision });
      say(`${step}: ${decision}`);
      await openRun(runId, true);
      setTimeout(() => state.current === runId && openRun(runId, true), 800); // the run goes on in the background
    } catch (error) {
      say(
        error instanceof ApiError && error.status === 403
          ? "This token may not answer this approval: it needs the title the step names (for the quarterly publication, chief statistician) or an admin."
          : problem(error),
      );
    }
  }

  // Why a run did not succeed, said once above the steps: the first step that failed, with the
  // code and the message the action gave, and for a goal that was not reached, which steps failed.
  function showFailure(report) {
    const box = $("run-failure");
    const failed = report.steps.filter((s) => s.state === "FAILED");
    const outcome = report.outcome || report.status;
    box.replaceChildren();
    box.hidden = failed.length === 0 || !["FAILED", "GOAL_NOT_REACHED", "COMPENSATED"].includes(outcome);
    if (box.hidden) return;
    const first = failed[0];
    const title = document.createElement("strong");
    const detail = document.createElement("span");
    if (outcome === "GOAL_NOT_REACHED") {
      const names = failed.slice(0, 6).map((s) => s.step).join(", ");
      title.textContent = "The goal was not reached";
      detail.textContent = `${failed.length} step(s) failed (${names}${failed.length > 6 ? ", …" : ""}). ${first.code || ""}${first.message ? ": " + first.message : ""}`;
    } else {
      title.textContent = `The run stopped at ${first.step}`;
      detail.textContent = `${first.code || ""}${first.message ? ": " + first.message : ""}`;
    }
    box.dataset.step = first.step;
    box.append(title, detail);
  }

  function showDecisions(report) {
    const box = $("run-decision");
    box.replaceChildren();
    const waiting = report.steps.filter((s) => s.state === "WAITING");
    const shipped = state.origins.get(report.run_id);
    box.hidden = waiting.length === 0 || Boolean(shipped);
    if (box.hidden) return;
    for (const s of waiting) {
      const ask = document.createElement("div");
      ask.className = "ask";
      ask.dataset.testid = "ask";
      ask.dataset.step = s.step;
      const text = document.createElement("span");
      text.textContent = `The step ${s.step} waits for an answer.`;
      const spacer = document.createElement("span");
      spacer.className = "spacer";
      ask.append(text, spacer);
      for (const [decision, label] of [["approved", "Approve"], ["rejected", "Reject"]]) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = label;
        button.className = decision === "approved" ? "approve" : "reject";
        button.dataset.decision = decision;
        button.addEventListener("click", () => decide(report.run_id, s.step, decision));
        ask.append(button);
      }
      box.append(ask);
    }
  }

  async function openRun(id, quiet = false) {
    if (!quiet) say("");
    state.current = id;
    for (const tr of $("runs-rows").children) tr.classList.toggle("current", tr.dataset.id === id);
    const steps = $("run-steps");
    try {
      const report = await call("GET", `/runs/${encodeURIComponent(id)}`);
      if (state.current !== id) return; // another run was opened while this one was fetched
      $("run-title").textContent = `Run ${report.run_id}`;
      $("run-summary").textContent = `${report.workflow} v${report.version}: ${report.outcome || report.status}`;
      $("run-empty").hidden = true;
      steps.replaceChildren();
      for (const s of report.steps) {
        const error = s.code ? `${s.code}${s.message ? ": " + s.message : ""}` : "";
        const line = row("step", s.step, [cell(s.step), cell(s.state, kindOf(s.state)), cell(String(s.attempts)), cell(error)]);
        if (s.state === "FAILED") line.classList.add("failed");
        steps.append(line);
      }
      showFailure(report);
      showDecisions(report);
      await showGraph(report);
    } catch (error) {
      if (quiet) return; // a refresh that fails leaves what is shown; the next one tries again
      steps.replaceChildren();
      $("run-decision").hidden = true;
      $("run-failure").hidden = true;
      $("run-graph-pane").hidden = true;
      $("run-empty").hidden = false;
      $("run-empty").textContent = problem(error);
    }
  }

  $("runs-refresh").addEventListener("click", loadRuns);
  $("services-refresh").addEventListener("click", () => loadServices());
  $("runs-from").addEventListener("change", () => {
    state.from = $("runs-from").value;
    loadRuns();
  });

  return { loadRuns, loadServices, openRun, live, liveServices };
}
