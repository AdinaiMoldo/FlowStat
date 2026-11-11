"""http.call: one HTTP request to another system, with the idempotency key of the operation.

Standard library only (``urllib``). The answer is classified for the retry policy of the step
(docs/dsl-spec.md 5.3): a transport failure, a timeout and a 5xx answer are transient (E-ACT-008),
a 4xx answer is permanent (E-ACT-010). The header ``Idempotency-Key`` carries the key of the
operation, which is the same for every attempt, so a receiver that honours it applies the effect
once even when the answer of the first attempt was lost.

Whether the receiver honours the key is outside this engine: the action is registered as
idempotent on the promise that it does (docs/dsl-spec.md 5.5, limits).

With a list of hosts (US-39) a call to another host fails before anything is sent (E-ACT-011,
permanent), and a redirect to another host is not followed. Without a list the action reaches any
host, as it did.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from collections.abc import Callable

from flowstat.actions.base import ActionContext, ActionError, Args
from flowstat.model.hosts import host_of

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


def _refuse(host: str | None, hosts: frozenset[str]) -> ActionError:
    shown = host if host else "a host that cannot be read"
    return ActionError(
        "E-ACT-011",
        f"http.call: the host {shown} is not on the list of allowed hosts "
        f"({', '.join(sorted(hosts))})",
    )


class _KeepToTheList(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to a host of the list: a redirect is a second request, to an
    address that the workflow did not write."""

    def __init__(self, hosts: frozenset[str]) -> None:
        self.hosts = hosts

    def redirect_request(  # type: ignore[no-untyped-def]
        self, req, fp, code, msg, headers, newurl
    ):
        host = host_of(newurl)
        if host not in self.hosts:
            raise _refuse(host, self.hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def calling(
    hosts: frozenset[str] | None = None,
) -> Callable[[Args, ActionContext], dict[str, object]]:
    """``http.call`` that reaches the hosts of ``hosts`` only; None: any host."""

    def call(args: Args, ctx: ActionContext) -> dict[str, object]:
        url = str(args["url"])
        if not url.startswith(("http://", "https://")):
            raise ActionError("E-ACT-009", f"http.call: {url!r} is not an http or https address")
        if hosts is not None and host_of(url) not in hosts:
            raise _refuse(host_of(url), hosts)
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
        opener = urllib.request.build_opener(
            *([_KeepToTheList(hosts)] if hosts is not None else [])
        )
        try:
            with opener.open(request, timeout=timeout) as response:
                return {"status": response.status, "body": decode(response.read())}
        except urllib.error.HTTPError as error:  # an answer, with a status
            message = f"http.call: {method} {url} answered {error.code}"
            if error.code >= 500:
                raise ActionError("E-ACT-008", message, transient=True) from None
            raise ActionError("E-ACT-010", message) from None
        except TimeoutError:
            raise ActionError(
                "E-ACT-008",
                f"http.call: {method} {url} timed out after {timeout:g} s",
                transient=True,
            ) from None
        except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
            # no answer: the request may or may not have taken effect, which is why the key is sent
            reason = getattr(error, "reason", error)
            raise ActionError(
                "E-ACT-008", f"http.call: {method} {url} got no answer ({reason})", transient=True
            ) from None

    return call


call = calling(None)
