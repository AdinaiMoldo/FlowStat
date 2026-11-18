// Calls to the API of the core. The token of a session (or an API token the person pastes) lives in
// sessionStorage of this tab only: it is not in the address, not in localStorage and not in any
// file of the page.

const KEY = "flowstat.token";

export const session = {
  get token() {
    try {
      return sessionStorage.getItem(KEY) || "";
    } catch {
      return "";
    }
  },
  set(token) {
    try {
      sessionStorage.setItem(KEY, token);
    } catch {
      /* a browser that refuses storage: the token then lasts until the page is reloaded */
      memory = token;
    }
  },
  clear() {
    memory = "";
    try {
      sessionStorage.removeItem(KEY);
    } catch {
      /* nothing to remove */
    }
  },
};

let memory = "";

function token() {
  return session.token || memory;
}

// Called once when the core refuses a token that the page holds (a session that has ended, a user
// that was disabled): the page then goes to the sign-in screen.
let onUnauthorized = null;

export function whenUnauthorized(handler) {
  onUnauthorized = handler;
}

export class ApiError extends Error {
  constructor(status, errors) {
    super(errors.map((e) => `${e.code} ${e.message}`).join("; "));
    this.status = status;
    this.errors = errors;
  }
}

export async function call(method, path, body) {
  const headers = { Accept: "application/json" };
  const held = token();
  if (held) headers.Authorization = `Bearer ${held}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    throw new ApiError(0, [{ code: "E-NET", message: "the server cannot be reached" }]);
  }
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!response.ok) {
    const errors = (data && data.errors) || [{ code: "E-HTTP", message: response.statusText }];
    if (response.status === 401 && held && path !== "/login" && onUnauthorized) onUnauthorized();
    throw new ApiError(response.status, errors);
  }
  return data;
}
