"""Request-level enforcement of the app password gate.

A middleware rather than a per-router dependency: the rule is about the HTTP
request (path prefix + method), not about any endpoint's arguments, and this
way a new router is covered the moment it is mounted instead of the day
somebody remembers to add a dependency to it.
"""

from __future__ import annotations

import re

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.services import session_auth

#: Never gated - the SPA needs both before anyone can possibly be logged in.
OPEN_PATHS = ("/api/health",)
OPEN_PREFIXES = ("/api/session",)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: Trade evaluation is a pure computation over data a viewer may already read,
#: so it stays available to them despite being a POST.
VIEWER_POST_ALLOWED = re.compile(r"^/api/leagues/[^/]+/evaluate/trade/?$")


def _is_open(path: str) -> bool:
    return path in OPEN_PATHS or any(
        path == prefix or path.startswith(f"{prefix}/") for prefix in OPEN_PREFIXES
    )


class SessionAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Static frontend files and anything outside the API are always
        # served: the SPA itself is what renders the login screen.
        if not session_auth.auth_enabled() or not path.startswith("/api"):
            return await call_next(request)

        if _is_open(path):
            return await call_next(request)

        role = session_auth.verify_token(request.cookies.get(session_auth.COOKIE_NAME))
        if role is None:
            return JSONResponse(
                {"detail": "Sign in to use Gridiron HQ."}, status_code=401
            )

        if (
            role == session_auth.ROLE_VIEWER
            and request.method.upper() not in SAFE_METHODS
            and not VIEWER_POST_ALLOWED.match(path)
        ):
            return JSONResponse(
                {"detail": "Read-only access: only the owner can change things."},
                status_code=403,
            )

        return await call_next(request)
