"""The app's own password gate: log in, log out, who am I.

Distinct from app.routers.auth, which is the Yahoo OAuth handshake.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app.services import session_auth

router = APIRouter(prefix="/api/session", tags=["session"])


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    role: str


class MeResponse(BaseModel):
    auth_enabled: bool
    role: str | None = None


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, response: Response) -> LoginResponse:
    role = session_auth.role_for_password(payload.password)
    if role is None:
        raise HTTPException(status_code=401, detail="Incorrect password.")

    response.set_cookie(
        session_auth.COOKIE_NAME,
        session_auth.create_token(role),
        max_age=session_auth.TOKEN_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        # No secure flag: TLS terminates at Tailscale, and the app is also
        # reachable over plain http on the LAN.
        path="/",
    )
    return LoginResponse(role=role)


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(session_auth.COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
def me(request: Request) -> MeResponse:
    enabled = session_auth.auth_enabled()
    if not enabled:
        return MeResponse(auth_enabled=False, role=None)
    role = session_auth.verify_token(request.cookies.get(session_auth.COOKIE_NAME))
    return MeResponse(auth_enabled=True, role=role)
