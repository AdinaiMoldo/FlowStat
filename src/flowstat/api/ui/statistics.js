// The view of statistics: the failure rate and the duration of the runs of each workflow.
// Numbers come from the server and are put into the page as text, never as markup.
import { ApiError, call } from "./api.js";

const $ = (id) => document.getElementById(id);

function problem(error) {
  if (error instanceof ApiError) {
    if (error.status === 401) return "sign in: paste a token and press Use";
    if (error.status === 403) return "only an admin may read the statistics";
    if (error.status === 0) return "the server cannot be reached";
    return error.message;
  }
  return String(error);
}

export function percent(rate) {
  if (rate === null || rate === undefined) return "-";
  const value = rate * 100;
  return `${Number.isInteger(value) ? value : value.toFixed(1)}%`;
}

export function seconds(value) {
  if (value === null || value === undefined) return "-";
  return `${value.toFixed(1)} s`;
}

function versions(map) {
  return Object.entries(map)
    .map(([number, v]) => `v${number}: ${v.runs} (${v.failed} failed)`)
    .join(", ");
}

function row(entry) {
  const tr = document.createElement("tr");
  tr.dataset.testid = "statistics-workflow";
  tr.dataset.workflow = entry.workflow;
  const cells = [
    entry.workflow,
    entry.runs,
    entry.running,
    entry.finished,
    entry.succeeded,
    entry.not_reached,
    entry.failed,
    percent(entry.failure_rate),
    seconds(entry.average_duration),
    versions(entry.versions),
  ];
  const names = [
    "workflow", "runs", "running", "finished", "succeeded", "not-reached", "failed", "rate",
    "duration", "versions",
  ];
  cells.forEach((value, i) => {
    const td = document.createElement("td");
    td.dataset.col = names[i];
    td.textContent = String(value);
    tr.append(td);
  });
  return tr;
}

export function createStatistics() {
  async function load() {
    $("statistics-status").textContent = "loading";
    try {
      const body = await call("GET", "/statistics");
      $("statistics-rows").replaceChildren(...body.workflows.map(row));
      $("statistics-status").textContent = body.workflows.length
        ? ""
        : "no workflow has runs yet";
    } catch (error) {
      $("statistics-rows").replaceChildren();
      $("statistics-status").textContent = problem(error);
    }
  }
  $("statistics-refresh").addEventListener("click", load);
  return { load };
}
