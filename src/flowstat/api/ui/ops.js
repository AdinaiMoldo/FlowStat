// The views of operations: the runs the core knows (its own and those shipped by services) and
// the services that were deployed. Everything that comes from a service (a message, an input) is
// put into the page as text, never as markup.
import { ApiError, call } from "./api.js";
import { createLive } from "./live.js";

const $ = (id) => document.getElementById(id);

const GOOD = new Set(["SUCCEEDED", "FINISHED", "RUNNING", "running"]);
const BAD = new Set([
  "FAILED", "GOAL_NOT_REACHED", "failed", "unreachable", "COMPENSATED", "COMPENSATION_FAILED",
]);

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

function kindOf(word) {
  if (GOOD.has(word)) return "good";
  if (BAD.has(word)) return "bad";
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
    if (error.status === 401) return "sign in: paste a token and press Use";
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
  const state = { from: "", current: null, services: [] };

  // The table of services. ``quiet`` is a refresh the person did not ask for: when the server
  // cannot be asked the table stays as it is. Returns false when asking again is pointless.
  async function loadServices(quiet = false) {
    const rows = $("services-rows");
    try {
      state.services = await call("GET", "/deployments");
      $("services-status").textContent = `${state.services.length} service(s)`;
    } catch (error) {
      if (!quiet) {
        state.services = [];
        rows.replaceChildren();
      }
      $("services-status").textContent = problem(error);
      return !(error instanceof ApiError && error.status === 401);
    }
    rows.replaceChildren();
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
        const status = r.outcome || r.status;
        const tr = row("run", r.run_id, [
          cell(r.run_id),
          cell(`${r.workflow} v${r.version}`),
          cell(status, kindOf(status)),
          cell(r.origin ? `service ${r.origin}` : "core"),
          cell(r.created_at),
        ]);
        if (state.current === r.run_id) tr.classList.add("current");
        tr.addEventListener("click", () => openRun(r.run_id));
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

  async function openRun(id, quiet = false) {
    state.current = id;
    for (const tr of $("runs-rows").children) tr.classList.toggle("current", tr.dataset.id === id);
    const steps = $("run-steps");
    try {
      const report = await call("GET", `/runs/${encodeURIComponent(id)}`);
      $("run-title").textContent = `Run ${report.run_id}`;
      $("run-summary").textContent = `${report.workflow} v${report.version}: ${report.outcome || report.status}`;
      $("run-empty").hidden = true;
      steps.replaceChildren();
      for (const s of report.steps) {
        const error = s.code ? `${s.code}${s.message ? ": " + s.message : ""}` : "";
        steps.append(
          row("step", s.step, [cell(s.step), cell(s.state, kindOf(s.state)), cell(String(s.attempts)), cell(error)]),
        );
      }
    } catch (error) {
      if (quiet) return; // a refresh that fails leaves what is shown; the next one tries again
      steps.replaceChildren();
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
