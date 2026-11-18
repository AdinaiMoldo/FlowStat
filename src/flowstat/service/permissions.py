"""Which role may do what (A3). The same table is in docs/api-spec.md, section 3, and a test
compares the two."""

from __future__ import annotations

PUBLIC = "public"  # a route with no permission: the health check and the sign-in

PERMISSIONS: dict[str, frozenset[str]] = {
    "session:use": frozenset({"admin", "designer", "operator"}),
    "workflows:read": frozenset({"admin", "designer", "operator"}),
    "workflows:write": frozenset({"admin", "designer"}),
    "workflows:publish": frozenset({"admin", "designer"}),
    "catalogue:write": frozenset({"admin"}),
    "runs:read": frozenset({"admin", "designer", "operator"}),
    "runs:start": frozenset({"admin", "designer", "operator"}),
    "runs:decide": frozenset({"admin", "operator"}),
    "deployments:read": frozenset({"admin", "designer", "operator"}),
    "deployments:write": frozenset({"admin"}),
    "runs:migrate": frozenset({"admin", "operator"}),
    "statistics:read": frozenset({"admin"}),
    "forms:read": frozenset({"admin", "designer", "operator"}),
    "forms:write": frozenset({"admin", "operator"}),
    "campaigns:read": frozenset({"admin", "designer", "operator"}),
    "campaigns:write": frozenset({"admin"}),
    "handover:control": frozenset({"admin"}),
    # Held by no role: it belongs to the ingest key of one deployment (api/deps.py).
    "events:ingest": frozenset(),
}


def allowed(role: str, permission: str) -> bool:
    return role in PERMISSIONS.get(permission, frozenset())
