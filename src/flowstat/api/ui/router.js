// The addresses of the pages (US-47). The page is one HTML file; where the person is lives in the
// part after "#", so that the back and forward buttons, a reload and a link all work:
//
//   #/home   #/campaigns[/<campaign>]   #/problems[/<problem>]   #/editor[/<workflow>]   #/forms   #/runs[/<run>]   #/services   #/migration
//   #/statistics
//   #/login?next=<address to go to after signing in>
//
// A query (for example "?example") is a one-time request to the page and is removed from the
// address once it has been done.

export const VIEWS = [
  "home", "campaigns", "problems", "editor", "forms", "runs", "services", "migration", "statistics", "login",
];

function decode(text) {
  try {
    return decodeURIComponent(text);
  } catch {
    return text; // a malformed address is shown as it is, not a crash
  }
}

// The route of an address. An empty or unknown address is the home page (``known`` is false, so
// that the page can put the right address in its place).
export function parse(hash) {
  const text = hash.replace(/^#\/?/, "");
  const cut = text.indexOf("?");
  const path = cut < 0 ? text : text.slice(0, cut);
  const params = new URLSearchParams(cut < 0 ? "" : text.slice(cut + 1));
  const [view, ...rest] = path.split("/");
  const known = VIEWS.includes(view);
  return {
    view: known ? view : "home",
    known,
    arg: known && rest.length ? decode(rest.join("/")) : null,
    params,
  };
}

// The address of a view, with an optional argument (a workflow, a run) and query.
export function href(view, arg = null, query = "") {
  const tail = arg === null ? "" : `/${encodeURIComponent(arg)}`;
  return `#/${view}${tail}${query ? `?${query}` : ""}`;
}

// Where to go after signing in: the address the person wanted, when it is one of ours and is not
// the sign-in page itself.
export function nextOf(route) {
  const wanted = route.params.get("next") || "";
  if (!wanted.startsWith("/")) return href("home");
  const target = parse(`#${wanted}`);
  return target.known && target.view !== "login" ? `#${wanted}` : href("home");
}
