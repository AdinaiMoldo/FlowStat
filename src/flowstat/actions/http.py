"""http.call: one HTTP request to another system, with the idempotency key of the operation.

Standard library only (``urllib``). The answer is classified for the retry policy of the step
(docs/dsl-spec.md 5.3): a transport failure, a timeout and a 5xx answer are transient (E-ACT-008),
a 4xx answer is permanent (E-ACT-010). The header ``Idempotency-Key`` carries the key of the
operation, which is the same for every attempt, so a receiver that honours it applies the effect
once even when the answer of the first attempt was lost.

Whether the receiver honours the key is outside this engine: the action is registered as
idempotent on the promise that it does (docs/dsl-spec.md 5.5, limits).
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request

from flowstat.actions.base import ActionContext, ActionError, Args

METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
DEFAULT_TIMEOUT = 5.0


def decode(raw: bytes) -> object:
    """JSON when the answer is JSON, else its text; nothing for an empty answer."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw.decode("utf8", errors="replace")


def _timeout(args: Args) -> float:
    value = args.get("timeout", DEFAULT_TIMEOUT)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ActionError("E-ACT-009", "http.call: 'timeout' must be a positive number of seconds")
    return float(value)


def call(args: Args, ctx: ActionContext) -> dict[str, object]:
    url = str(args["url"])
    if not url.startswith(("http://", "https://")):
        raise ActionError("E-ACT-009", f"http.call: {url!r} is not an http or https address")
    method = str(args.get("method", "POST")).upper()
    if method not in METHODS:
        raise ActionError(
            "E-ACT-009", f"http.call: method {method} is not one of {sorted(METHODS)}"
        )
    timeout = _timeout(args)
    body = args.get("body")
    data = None if body is None or method == "GET" else json.dumps(body).encode()
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Idempotency-Key": ctx.operation_key},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {"status": response.status, "body": decode(response.read())}
    except urllib.error.HTTPError as error:  # an answer, with a status
        message = f"http.call: {method} {url} answered {error.code}"
        if error.code >= 500:
            raise ActionError("E-ACT-008", message, transient=True) from None
        raise ActionError("E-ACT-010", message) from None
    except TimeoutError:
        raise ActionError(
            "E-ACT-008", f"http.call: {method} {url} timed out after {timeout:g} s", transient=True
        ) from None
    except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
        # no answer: the request may or may not have taken effect, which is why the key is sent
        reason = getattr(error, "reason", error)
        raise ActionError(
            "E-ACT-008", f"http.call: {method} {url} got no answer ({reason})", transient=True
        ) from None
