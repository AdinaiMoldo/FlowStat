"""Which host a URL names, and the list of hosts that may be called (US-39).

The check at save (a constant URL in a workflow) and the check of the call (the URL after the
arguments were rendered) both use ``host_of``, so the two cannot disagree about what a host is.
The host is the one the request goes to: the part after ``@`` when the URL has a user, and without
port, case or a final dot. ``https://api.example.kg@evil.example/`` names ``evil.example``.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

ALLOWED_HOSTS = "FLOWSTAT_ALLOWED_HOSTS"  # the list as the environment of a process gives it


def normalize(host: str) -> str:
    return host.strip().lower().rstrip(".")


def parse_hosts(text: str | None) -> frozenset[str] | None:
    """``"a.kg, b.kg"`` as a set of hosts; None (no list: any host) when there is nothing in it."""
    if not text:
        return None
    found = {normalize(part) for part in text.split(",")}
    found.discard("")
    return frozenset(found) or None


def hosts_from(env: Mapping[str, str]) -> frozenset[str] | None:
    """The list a process was started with: ``FLOWSTAT_ALLOWED_HOSTS=a.kg,b.kg``."""
    return parse_hosts(env.get(ALLOWED_HOSTS))


def host_of(url: str) -> str | None:
    """The host a URL goes to, or None when it names none (no scheme, no host, not readable)."""
    try:
        parts = urlsplit(url)
        name = parts.hostname
    except ValueError:
        return None
    return normalize(name) if parts.scheme and name else None


def leading_host(parts: tuple[object, ...]) -> str | None:
    """The host of an argument whose text begins with a constant URL, when the host is wholly
    written there. ``('https://a.kg/v1/', <reference>)`` names ``a.kg``; ``('https://', <ref>)``
    and ``('https://a.', <ref>)`` name none that is known before the run."""
    if not parts or not isinstance(parts[0], str):
        return None
    first = parts[0]
    if len(parts) > 1:  # the text goes on with a reference: the host must have ended before it
        _, found, rest = first.partition("://")
        if not found or not any(mark in rest for mark in "/?#"):
            return None
    return host_of(first)
