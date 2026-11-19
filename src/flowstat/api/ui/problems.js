// The view of the problems of the forms (US-56): every error of a new version of a form is a
// problem with a status, someone who works on it and a history. The page lists them, shows one with
// its history, and lets a person who may write give it to a user or add a comment. What the server
// sends is put into the page as text, never as markup. The life cycle is the server's.
import { ApiError, call } from "./api.js";
import { plural, when } from "./format.js";

const $ = (id) => document.getElementById(id);

const KINDS = { OPEN: "bad", REOPENED: "bad", REVALIDATING: "wait", RESOLVED: "good" };
const EVENTS = {
  OPENED: "opened",
  ASSIGNED: "assigned",
  COMMENTED: "commented",
  REVALIDATING: "checked again",
  RESOLVED: "resolved",
  REOPENED: "reopened",
};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function badge(status) {
  return element("span", `badge ${KINDS[status] || ""}`, status.toLowerCase());
}

function words(error) {
  if (error instanceof ApiError) {
    if (error.status === 401) return "your session has ended: sign in again";
    if (error.status === 0) return "the server cannot be reached";
    return error.message;
  }
  return String(error);
}

export function createProblems({ goTo }) {
  let user = null;
  let problems = [];
  let shown = null; // the id of the problem in the right panel
  let detail = null;

  const can = (permission) => user !== null && user.permissions.includes(permission);

  function row(p) {
    const tr = element("tr");
    tr.dataset.testid = "problem";
    tr.dataset.id = p.id;
    tr.dataset.status = p.status;
    const status = element("td");
    status.append(badge(p.status));
    const what = element("td", "", `${p.code} ${p.indicator || p.cell}`);
    what.title = p.message;
    tr.append(
      status,
      element("td", "", `${p.region} ${p.quarter}`),
      what,
      element("td", "", p.assignee || "–"),
      element("td", "", when(p.updated_at)),
    );
    tr.classList.toggle("current", p.id === shown);
    tr.addEventListener("click", () => goTo(`problems/${encodeURIComponent(p.id)}`));
    return tr;
  }

  function renderList() {
    $("problems-rows").replaceChildren(...problems.map(row));
    $("problems-table").hidden = problems.length === 0;
    $("problems-empty").hidden = problems.length > 0;
    $("problems-status").textContent = problems.length ? plural(problems.length, "problem") : "";
  }

  function event(e) {
    const li = element("li");
    li.dataset.testid = "problem-event";
    li.dataset.kind = e.kind;
    const head = `${EVENTS[e.kind] || e.kind} by ${e.by}`;
    li.append(element("strong", "", head));
    if (e.version !== null) li.append(element("span", "muted", ` · version ${e.version}`));
    li.append(element("span", "muted", ` · ${when(e.at)}`));
    if (e.text) li.append(element("div", "", e.text));
    return li;
  }

  function renderDetail() {
    const body = $("problem-body");
    body.hidden = detail === null;
    $("problem-choose").hidden = detail !== null;
    if (detail === null) {
      $("problem-state").replaceChildren();
      return;
    }
    $("problem-state").replaceChildren(badge(detail.status));
    $("problem-title").textContent = `${detail.region} ${detail.quarter}: ${detail.code}`;
    $("problem-message").textContent = detail.message;
    const facts = [`cell ${detail.cell}`, `seen in version ${detail.seen_in}`];
    if (detail.actual || detail.expected) facts.push(`found ${detail.actual || "–"}, expected ${detail.expected || "–"}`);
    $("problem-facts").textContent = facts.join(" · ");
    $("problem-assignee").textContent = detail.assignee || "nobody";
    $("problem-events").replaceChildren(...detail.events.map(event));
    const writable = can("problems:write");
    $("problem-write").hidden = !writable;
    $("problem-assign-form").hidden = detail.status === "RESOLVED";
    $("problem-assign-input").value = detail.assignee || "";
    $("problem-note").hidden = true;
  }

  function note(text) {
    const n = $("problem-note");
    n.textContent = text;
    n.hidden = false;
  }

  async function change(method, path, body, after) {
    try {
      detail = await call(method, path, body);
    } catch (error) {
      note(words(error));
      return;
    }
    after();
    renderDetail();
    await reload();
  }

  async function assign(name) {
    await change("POST", `/problems/${encodeURIComponent(shown)}/assignee`, { assignee: name }, () => {});
  }

  async function comment(event) {
    event.preventDefault();
    const text = $("problem-comment-text");
    await change("POST", `/problems/${encodeURIComponent(shown)}/comments`, { text: text.value }, () => {
      text.value = "";
    });
  }

  async function reload() {
    const query = new URLSearchParams();
    if ($("problems-active").checked) query.set("active", "true");
    try {
      problems = (await call("GET", `/problems${query.size ? `?${query}` : ""}`)).problems;
    } catch (error) {
      $("problems-status").textContent = words(error);
      return;
    }
    renderList();
  }

  async function open(id) {
    shown = id;
    detail = null;
    if (id) {
      try {
        detail = await call("GET", `/problems/${encodeURIComponent(id)}`);
      } catch (error) {
        $("problems-status").textContent = words(error);
      }
    }
    renderList();
    renderDetail();
  }

  async function load(who, id) {
    user = who;
    for (const node of $("view-problems").querySelectorAll("[data-needs]")) {
      node.hidden = !can(node.dataset.needs);
    }
    await reload();
    await open(id ?? shown);
  }

  $("problems-active").addEventListener("change", reload);
  $("problem-assign-form").addEventListener("submit", (e) => {
    e.preventDefault();
    assign($("problem-assign-input").value.trim() || null);
  });
  $("problem-assign-me").addEventListener("click", () => assign(user.name));
  $("problem-comment-form").addEventListener("submit", comment);
  return { load };
}
