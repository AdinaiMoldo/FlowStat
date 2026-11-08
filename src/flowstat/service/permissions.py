"""Which role may do what (A3). The same table is in docs/api-spec.md, section 3, and a test
compares the two."""

from __future__ import annotations

PUBLIC = "public"  # a route with no permission: only the health check

PERMISSIONS: dict[str, frozenset[str]] = {
    "workflows:read": frozenset({"admin", "designer", "operator"}),
    "workflows:write": frozenset({"admin", "designer"}),
    "workflows:publish": frozenset({"admin", "designer"}),
    "catalogue:write": frozenset({"admin"}),
    "runs:read": frozenset({"admin", "designer", "operator"}),
    "runs:start": frozenset({"admin", "designer", "operator"}),
    "runs:decide": frozenset({"admin", "operator"}),
    "deployments:read": frozenset({"admin", "designer", "operator"}),
    "deployments:write": frozenset({"admin"}),
}


def allowed(role: str, permission: str) -> bool:
    return role in PERMISSIONS.get(permission, frozenset())
