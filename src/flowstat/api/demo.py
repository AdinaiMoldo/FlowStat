"""The buttons "sign in as" of a demonstration (US-45, docs/api-spec.md section 9).

Off by default. The core is started with ``--demo-logins FILE`` and only then does this route exist.
It hands the tokens of the demonstration users to whoever asks, without a token, so that the page
can show a button for each role instead of asking for a token to be pasted. That is why the
executable refuses the option unless the server listens on the loopback address only: anyone who
can reach the port can read the tokens.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from flowstat.api.deps import config_of
from flowstat.api.secured import SecuredRouter
from flowstat.service.permissions import PUBLIC

router = SecuredRouter(tags=["demo"])


@router.secured("GET", "/demo/logins", PUBLIC, include_in_schema=False)
def logins(request: Request) -> JSONResponse:
    found = config_of(request).demo_logins or {}
    return JSONResponse(
        {"logins": [{"name": name, "token": token} for name, token in found.items()]},
        headers={"Cache-Control": "no-store"},
    )
