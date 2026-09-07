"""Stateless signed session tokens for the app's password gate.

There are no user accounts: the owner knows one password, league mates know
another (or none at all). A successful login hands back a cookie carrying
nothing but a role and an expiry, signed with HMAC-SHA256 so the server can
trust it again without storing anything.

The whole gate is off whenever ``OWNER_PASSWORD`` is empty - that is the
default, and it is what local development and the test suite run under.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from app.config import settings

#: Name of the cookie carrying the signed token.
COOKIE_NAME = "ghq_session"

#: How long a login lasts, in seconds.
TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60

ROLE_OWNER = "owner"
ROLE_VIEWER = "viewer"

#: Fallback signing key, generated once per process. Used only when
#: SESSION_SECRET is unset; sessions then die with the process, which is an
#: acceptable trade for never shipping a hard-coded default key.
_EPHEMERAL_SECRET = secrets.token_hex(32)


def auth_enabled() -> bool:
    """True when the password gate should be enforced."""
    return bool(settings.OWNER_PASSWORD)


def _secret() -> bytes:
    return (settings.SESSION_SECRET or _EPHEMERAL_SECRET).encode("utf-8")


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def create_token(role: str, ttl_seconds: int = TOKEN_TTL_SECONDS) -> str:
    """A signed ``role.expiry.signature`` token."""
    expires_at = int(time.time()) + ttl_seconds
    payload = f"{role}.{expires_at}"
    return f"{payload}.{_sign(payload)}"


def verify_token(token: str | None) -> str | None:
    """The role carried by ``token``, or None if it is absent, tampered with, or expired."""
    if not token:
        return None

    parts = token.split(".")
    if len(parts) != 3:
        return None
    role, raw_expiry, signature = parts

    if not hmac.compare_digest(_sign(f"{role}.{raw_expiry}"), signature):
        return None

    try:
        expires_at = int(raw_expiry)
    except ValueError:
        return None
    if expires_at <= int(time.time()):
        return None

    if role not in (ROLE_OWNER, ROLE_VIEWER):
        return None
    return role


def role_for_password(password: str) -> str | None:
    """The role a submitted password unlocks, or None when it matches neither.

    Compared in constant time, and an empty configured password never matches
    (otherwise an unset LEAGUE_PASSWORD would let anyone in with "").
    """
    owner = settings.OWNER_PASSWORD
    league = settings.LEAGUE_PASSWORD

    # Both comparisons always run so the answer's timing does not reveal
    # which password was close.
    is_owner = bool(owner) and hmac.compare_digest(password, owner)
    is_viewer = bool(league) and hmac.compare_digest(password, league)

    if is_owner:
        return ROLE_OWNER
    if is_viewer:
        return ROLE_VIEWER
    return None
