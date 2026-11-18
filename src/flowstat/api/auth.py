"""Sign in with a name and a password, see who is signed in, sign out (US-46, api-spec section 2).

``POST /login`` is public: it is how a person gets a token. Failed attempts are counted for each
name in memory, so that a password cannot be guessed at the speed of the network: after
``ATTEMPTS`` failures within ``WINDOW`` seconds the answer is 429 until the window has passed, even
for the right password. The count is in memory on purpose (it is a brake, not a record); a restart
of the core resets it. The clock that measures the window comes from the configuration.
"""

from __future__ import annotations

import sqlite3
import threading

from fastapi import Depends, Request, Response

from flowstat.api.deps import config_of, get_db
from flowstat.api.errors import fail
from flowstat.api.models import LoginIn, MeOut, SessionOut
from flowstat.api.secured import SecuredRouter
from flowstat.repo.users import User
from flowstat.service.permissions import PERMISSIONS, PUBLIC
from flowstat.service.users import SESSION_HOURS, UserService, later

router = SecuredRouter(tags=["auth"])

ATTEMPTS = 5
WINDOW = 60.0  # seconds
TRACKED = 1000  # names counted at the same time; the oldest is forgotten first


class Brake:
    """The failed attempts of the last ``WINDOW`` seconds, for each name."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.failures: dict[str, list[float]] = {}

    def blocked(self, name: str, now: float) -> bool:
        with self.lock:
            recent = [t for t in self.failures.get(name, []) if now - t < WINDOW]
            if recent:
                self.failures[name] = recent
            else:
                self.failures.pop(name, None)
            return len(recent) >= ATTEMPTS

    def failed(self, name: str, now: float) -> None:
        with self.lock:
            self.failures.setdefault(name, []).append(now)
            while len(self.failures) > TRACKED:
                self.failures.pop(next(iter(self.failures)))

    def forget(self, name: str) -> None:
        with self.lock:
            self.failures.pop(name, None)


def brake_of(request: Request) -> Brake:
    brake: Brake = request.app.state.brake
    return brake


def permissions_of(role: str) -> list[str]:
    return sorted(p for p, roles in PERMISSIONS.items() if role in roles and p != "events:ingest")


@router.secured("POST", "/login", PUBLIC, summary="Sign in with a name and a password")
def login(body: LoginIn, request: Request, db: sqlite3.Connection = Depends(get_db)) -> SessionOut:
    config = config_of(request)
    brake = brake_of(request)
    if brake.blocked(body.name, config.time()):
        raise fail(429, "E-AUTH-003", "too many attempts; wait a minute and try again")
    now = config.clock()
    token = config.new_token()
    user = UserService(db).login(body.name, body.password, token, now)
    if user is None:
        brake.failed(body.name, config.time())
        raise fail(401, "E-AUTH-004", "the name or the password is wrong")
    brake.forget(body.name)
    return SessionOut(
        token=token,
        name=user.name,
        role=user.role,
        titles=sorted(user.titles),
        expires_at=later(now, SESSION_HOURS),
    )


@router.secured("GET", "/me", "session:use", summary="Who is signed in")
def me(request: Request) -> MeOut:
    user: User = request.state.user
    return MeOut(
        name=user.name,
        role=user.role,
        titles=sorted(user.titles),
        permissions=permissions_of(user.role),
    )


@router.secured("POST", "/logout", "session:use", status_code=204, summary="Sign out")
def logout(request: Request, db: sqlite3.Connection = Depends(get_db)) -> Response:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer":
        UserService(db).logout(token.strip())
    return Response(status_code=204)
