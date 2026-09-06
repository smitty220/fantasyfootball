"""Adapter that lets ``yahoo_fantasy_api`` run on *our* token management.

``yahoo_fantasy_api`` was written against ``yahoo_oauth.OAuth2``.  Reading its
source (``yhandler.YHandler``) shows the only thing it ever asks of the session
context object (``sc``) is:

* ``sc.session`` -- a ``requests.Session``-like object exposing
  ``get(url, params=...)``, ``put(url, data=..., headers=...)`` and
  ``post(url, data=..., headers=...)``, whose responses expose
  ``status_code``, ``content`` and ``json()``;
* on a 401/403 that looks like an expired token, the optional trio
  ``sc.refresh_access_token()`` -> dict with ``access_token``,
  ``sc.access_token`` (assignable) and ``sc.oauth.get_session(token=...)``.

``Game``/``League``/``Team`` only stash ``sc`` and hand it to ``YHandler``, so
satisfying that surface is enough -- no direct use of the library's OAuth flow.

:class:`YahooSessionContext` implements exactly that over httpx, pulling a
fresh bearer token from :func:`app.services.yahoo.oauth.get_valid_access_token`
before every request (which refreshes and persists the rotated token pair only
when the stored one is at/near expiry).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.services.yahoo import oauth

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0


class _BearerSession:
    """A minimal ``requests.Session`` stand-in backed by ``httpx.Client``.

    The bearer token is resolved per request, so a long sync that outlives the
    one-hour access token keeps working without the caller doing anything.
    """

    def __init__(self, db: Session, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._db = db
        self._client = httpx.Client(timeout=timeout, follow_redirects=True)

    def _headers(self, headers: dict[str, str] | None = None) -> dict[str, str]:
        merged = dict(headers or {})
        merged["Authorization"] = f"Bearer {oauth.get_valid_access_token(self._db)}"
        return merged

    @staticmethod
    def _body_kwargs(data: Any) -> dict[str, Any]:
        # httpx wants raw XML/text bodies as ``content``, not ``data``.
        if isinstance(data, (str, bytes)):
            return {"content": data}
        if data is None:
            return {}
        return {"data": data}

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        return self._client.get(
            url, params=params, headers=self._headers(headers), **kwargs
        )

    def put(
        self,
        url: str,
        data: Any = None,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        return self._client.put(
            url, headers=self._headers(headers), **self._body_kwargs(data), **kwargs
        )

    def post(
        self,
        url: str,
        data: Any = None,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        return self._client.post(
            url, headers=self._headers(headers), **self._body_kwargs(data), **kwargs
        )

    def close(self) -> None:
        self._client.close()


class _OAuthShim:
    """Stands in for ``yahoo_oauth.OAuth2.oauth`` on the library's retry path."""

    def __init__(self, session: _BearerSession) -> None:
        self._session = session

    def get_session(self, token: str | None = None) -> _BearerSession:
        # Our session resolves the token itself; ``token`` is ignored.
        return self._session


class YahooSessionContext:
    """The ``sc`` object handed to ``yahoo_fantasy_api`` Game/League/Team."""

    def __init__(self, db: Session, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._db = db
        self._session = _BearerSession(db, timeout=timeout)
        self.oauth = _OAuthShim(self._session)
        # Populated lazily; the library reads/assigns this on its retry path.
        self.access_token: str | None = None

    @property
    def session(self) -> _BearerSession:
        return self._session

    @session.setter
    def session(self, value: Any) -> None:
        # ``YHandler._refresh_token_and_retry`` reassigns ``sc.session`` after a
        # refresh.  We already hand back a self-refreshing session, so swallow
        # the assignment rather than let it raise.
        if isinstance(value, _BearerSession):
            self._session = value

    def refresh_access_token(self) -> dict[str, str]:
        """Force a refresh (used by the library after a 401)."""
        token = oauth.refresh_token(self._db)
        self.access_token = token.access_token
        return {"access_token": token.access_token}

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "YahooSessionContext":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def make_session_context(db: Session) -> YahooSessionContext:
    """Build a session context, failing fast if Yahoo was never connected."""
    # Surfaces YahooNotConnectedError up front instead of mid-sync.
    oauth.get_valid_access_token(db)
    return YahooSessionContext(db)
