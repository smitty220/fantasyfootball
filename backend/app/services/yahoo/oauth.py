"""Yahoo OAuth2 (authorization-code, out-of-band redirect).

Implemented in-house with httpx so that *we* own token storage: Yahoo rotates
the refresh token on every refresh, and both halves of the new pair must be
persisted before the old one is discarded.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import OAuthToken

logger = logging.getLogger(__name__)

PROVIDER = "yahoo"
AUTHORIZE_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
REDIRECT_URI = "oob"

#: Refresh this many seconds before the token actually expires.
EXPIRY_SKEW_SECONDS = 60

#: Yahoo access tokens live an hour; used only if the response omits expires_in.
DEFAULT_EXPIRES_IN = 3600


class YahooAuthError(RuntimeError):
    """Raised when Yahoo rejects an auth request."""


class YahooNotConnectedError(YahooAuthError):
    """Raised when no Yahoo token has been stored yet."""


def _utcnow() -> datetime:
    """Naive UTC now (the model columns are timezone-naive DateTime)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _require_credentials() -> tuple[str, str]:
    client_id = settings.YAHOO_CLIENT_ID
    client_secret = settings.YAHOO_CLIENT_SECRET
    if not client_id or not client_secret:
        raise YahooAuthError(
            "YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET are not configured."
        )
    return client_id, client_secret


def get_authorize_url() -> str:
    """Build the URL the owner opens to approve the app.

    Yahoo's out-of-band flow shows a code on screen that the owner pastes back
    into the app (see :func:`exchange_code`).
    """
    client_id, _ = _require_credentials()
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def get_token(db: Session) -> OAuthToken | None:
    """Return the stored Yahoo token row, or None if not connected."""
    return db.query(OAuthToken).filter(OAuthToken.provider == PROVIDER).one_or_none()


def _post_token_request(data: dict[str, str]) -> dict:
    client_id, client_secret = _require_credentials()
    try:
        response = httpx.post(
            TOKEN_URL,
            data=data,
            auth=(client_id, client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30.0,
        )
    except httpx.HTTPError as exc:  # network-level failure
        raise YahooAuthError(f"Yahoo token request failed: {exc}") from exc

    if response.status_code != 200:
        raise YahooAuthError(
            f"Yahoo token request failed ({response.status_code}): {response.text}"
        )

    payload = response.json()
    if "access_token" not in payload:
        raise YahooAuthError(f"Yahoo token response had no access_token: {payload}")
    return payload


def _persist(db: Session, payload: dict, existing: OAuthToken | None) -> OAuthToken:
    """Write the (possibly rotated) token pair to the DB in one transaction."""
    expires_in = int(payload.get("expires_in") or DEFAULT_EXPIRES_IN)
    now = _utcnow()

    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        # Yahoo always rotates, but never blow away a usable refresh token if
        # a response unexpectedly omits it.
        if existing is None:
            raise YahooAuthError("Yahoo token response had no refresh_token.")
        refresh_token = existing.refresh_token

    token = existing
    if token is None:
        token = OAuthToken(provider=PROVIDER)
        db.add(token)

    token.access_token = payload["access_token"]
    token.refresh_token = refresh_token
    token.expires_at = now + timedelta(seconds=expires_in)
    token.updated_at = now

    db.commit()
    db.refresh(token)
    return token


def exchange_code(code: str, db: Session) -> OAuthToken:
    """Exchange the pasted authorization code for a token pair and store it."""
    payload = _post_token_request(
        {
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
            "code": code,
        }
    )
    token = _persist(db, payload, get_token(db))
    logger.info("Stored Yahoo OAuth token (expires %s)", token.expires_at)
    return token


def refresh_token(db: Session, token: OAuthToken | None = None) -> OAuthToken:
    """Refresh the access token, persisting the newly rotated refresh token.

    Yahoo issues a *new* refresh token on every refresh and invalidates the old
    one, so the new pair is committed before this returns.
    """
    token = token or get_token(db)
    if token is None:
        raise YahooNotConnectedError("Yahoo is not connected; no token stored.")

    payload = _post_token_request(
        {
            "grant_type": "refresh_token",
            "redirect_uri": REDIRECT_URI,
            "refresh_token": token.refresh_token,
        }
    )
    token = _persist(db, payload, token)
    logger.info("Refreshed Yahoo OAuth token (expires %s)", token.expires_at)
    return token


def is_expired(token: OAuthToken, skew: int = EXPIRY_SKEW_SECONDS) -> bool:
    """True if the token is expired or expires within ``skew`` seconds."""
    if token.expires_at is None:
        return True
    return token.expires_at <= _utcnow() + timedelta(seconds=skew)


def get_valid_access_token(db: Session) -> str:
    """Return a usable access token, refreshing first when it is near expiry."""
    token = get_token(db)
    if token is None:
        raise YahooNotConnectedError("Yahoo is not connected; no token stored.")

    if is_expired(token):
        token = refresh_token(db, token)

    return token.access_token
