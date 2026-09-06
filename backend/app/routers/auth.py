"""Yahoo OAuth endpoints (owner-only, out-of-band code paste flow)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.yahoo import oauth

router = APIRouter(prefix="/api/auth/yahoo", tags=["auth"])


class YahooStatus(BaseModel):
    connected: bool
    expires_at: datetime | None = None


class AuthorizeUrl(BaseModel):
    authorize_url: str


class CallbackRequest(BaseModel):
    code: str


class CallbackResponse(BaseModel):
    connected: bool
    expires_at: datetime | None = None


@router.get("/status", response_model=YahooStatus)
def yahoo_status(db: Session = Depends(get_db)) -> YahooStatus:
    token = oauth.get_token(db)
    if token is None:
        return YahooStatus(connected=False)
    return YahooStatus(connected=True, expires_at=token.expires_at)


@router.get("/start", response_model=AuthorizeUrl)
def yahoo_start() -> AuthorizeUrl:
    try:
        return AuthorizeUrl(authorize_url=oauth.get_authorize_url())
    except oauth.YahooAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/callback", response_model=CallbackResponse)
def yahoo_callback(
    payload: CallbackRequest, db: Session = Depends(get_db)
) -> CallbackResponse:
    code = payload.code.strip()
    if not code:
        raise HTTPException(status_code=422, detail="Authorization code is required.")

    try:
        token = oauth.exchange_code(code, db)
    except oauth.YahooAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return CallbackResponse(connected=True, expires_at=token.expires_at)
