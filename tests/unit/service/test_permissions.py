"""The permission table in the code is the table in docs/api-spec.md, section 3."""

from __future__ import annotations

from pathlib import Path

from flowstat.service.permissions import PERMISSIONS, allowed
from flowstat.service.users import ROLES

SPEC = Path(__file__).resolve().parents[3] / "docs" / "api-spec.md"


def spec_table() -> dict[str, set[str]]:
    """permission -> roles that have it, read from the table under '## 3. Permissions'."""
    text = SPEC.read_text(encoding="utf8")
    section = text.split("## 3. Permissions", 1)[1].split("\n## ", 1)[0]
    rows = [r for r in section.splitlines() if r.startswith("| `")]
    out: dict[str, set[str]] = {}
    for row in rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        permission = cells[0].strip("`")
        flags = dict(zip(ROLES, cells[2:5], strict=True))
        out[permission] = {role for role, yes in flags.items() if yes == "yes"}
    return out


def test_the_code_and_the_spec_give_the_same_permissions() -> None:
    from_spec = spec_table()
    assert from_spec  # the table was found
    assert {p: set(roles) for p, roles in PERMISSIONS.items()} == from_spec


def test_the_admin_has_every_permission() -> None:
    assert all("admin" in roles for roles in PERMISSIONS.values())


def test_an_operator_cannot_write_or_publish_a_workflow() -> None:
    assert not allowed("operator", "workflows:publish")
    assert not allowed("operator", "workflows:write")
    assert allowed("operator", "runs:start") and allowed("operator", "runs:decide")
    assert allowed("designer", "workflows:publish") and not allowed("designer", "runs:decide")


def test_an_unknown_permission_or_role_is_never_allowed() -> None:
    assert not allowed("admin", "nothing:here")
    assert not allowed("root", "runs:read")
