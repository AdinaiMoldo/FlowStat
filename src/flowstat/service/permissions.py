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
    "consolidations:write": frozenset({"admin", "operator"}),
    "consolidations:decide": frozenset({"admin", "operator"}),  # and the title of the approver
    "problems:read": frozenset({"admin", "designer", "operator"}),
    "problems:write": frozenset({"admin", "operator"}),
    "handover:control": frozenset({"admin"}),
    # Held by no role: it belongs to the ingest key of one deployment (api/deps.py).
    "events:ingest": frozenset(),
}


def office_allows(role: str, offices: frozenset[str], region: str) -> bool:
    """Whether a user may change what belongs to the office of ``region`` (US-64).

    An operator who is bound to regional offices changes only what is theirs. An admin, and an
    operator of the central office (bound to none), are not limited. Reading is not limited.
    """
    return role != "operator" or not offices or region in offices


def allowed(role: str, permission: str) -> bool:
    return role in PERMISSIONS.get(permission, frozenset())
