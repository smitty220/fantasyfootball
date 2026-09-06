"""OAuth flow tests. All Yahoo HTTP is mocked; nothing leaves the machine."""

from __future__ import annotations

import base64
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from app.models import OAuthToken
from app.services.yahoo import oauth
from tests.conftest import make_token, utcnow

TOKEN_URL = oauth.TOKEN_URL


def _form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def _basic_auth(request: httpx.Request) -> tuple[str, str]:
    raw = request.headers["authorization"].split(" ", 1)[1]
    user, _, password = base64.b64decode(raw).decode().partition(":")
    return user, password


def test_get_authorize_url(yahoo_credentials):
    url = oauth.get_authorize_url()
    parsed = urlparse(url)
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

    assert url.startswith("https://api.login.yahoo.com/oauth2/request_auth?")
    from app.config import settings

    assert params == {
        "client_id": "test-client-id",
        "redirect_uri": settings.YAHOO_REDIRECT_URI,
        "response_type": "code",
    }


def test_get_authorize_url_without_credentials(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "YAHOO_CLIENT_ID", "")
    with pytest.raises(oauth.YahooAuthError):
        oauth.get_authorize_url()


@respx.mock
def test_exchange_code_stores_token(db_session, yahoo_credentials):
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "access-abc",
                "refresh_token": "refresh-abc",
                "expires_in": 3600,
                "token_type": "bearer",
            },
        )
    )

    token = oauth.exchange_code("the-code", db_session)

    assert route.called
    form = _form(route.calls.last.request)
    from app.config import settings

    assert form["grant_type"] == "authorization_code"
    assert form["redirect_uri"] == settings.YAHOO_REDIRECT_URI
    assert form["code"] == "the-code"
    assert _basic_auth(route.calls.last.request) == (
        "test-client-id",
        "test-client-secret",
    )

    rows = db_session.query(OAuthToken).all()
    assert len(rows) == 1
    assert rows[0].provider == "yahoo"
    assert rows[0].access_token == "access-abc"
    assert rows[0].refresh_token == "refresh-abc"
    assert token.expires_at > utcnow()


@respx.mock
def test_exchange_code_upserts_single_row(db_session, yahoo_credentials):
    make_token(db_session, "old-access", "old-refresh")
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
            },
        )
    )

    oauth.exchange_code("code", db_session)

    rows = db_session.query(OAuthToken).all()
    assert len(rows) == 1
    assert rows[0].access_token == "new-access"


@respx.mock
def test_exchange_code_raises_on_error(db_session, yahoo_credentials):
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )

    with pytest.raises(oauth.YahooAuthError):
        oauth.exchange_code("bad-code", db_session)

    assert db_session.query(OAuthToken).count() == 0


@respx.mock
def test_refresh_rotates_both_tokens(db_session, yahoo_credentials):
    make_token(db_session, "access-1", "refresh-1", expires_in_seconds=3600)
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "access-2",
                "refresh_token": "refresh-2",
                "expires_in": 3600,
            },
        )
    )

    oauth.refresh_token(db_session)

    # The refresh request used the *old* refresh token...
    assert _form(route.calls.last.request)["refresh_token"] == "refresh-1"

    # ...and the DB now holds only the new pair. Yahoo invalidates the old
    # refresh token, so keeping it around would be a footgun.
    db_session.expire_all()
    rows = db_session.query(OAuthToken).all()
    assert len(rows) == 1
    assert rows[0].access_token == "access-2"
    assert rows[0].refresh_token == "refresh-2"
    assert (
        db_session.query(OAuthToken)
        .filter(OAuthToken.refresh_token == "refresh-1")
        .count()
        == 0
    )


@respx.mock
def test_refresh_keeps_old_refresh_token_if_response_omits_it(
    db_session, yahoo_credentials
):
    make_token(db_session, "access-1", "refresh-1")
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "access-2", "expires_in": 3600}
        )
    )

    token = oauth.refresh_token(db_session)
    assert token.access_token == "access-2"
    assert token.refresh_token == "refresh-1"


@respx.mock
def test_get_valid_access_token_refreshes_when_expired(db_session, yahoo_credentials):
    token = make_token(db_session, "stale-access", "refresh-1")
    token.expires_at = utcnow() - timedelta(minutes=5)
    db_session.commit()

    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "fresh-access",
                "refresh_token": "refresh-2",
                "expires_in": 3600,
            },
        )
    )

    assert oauth.get_valid_access_token(db_session) == "fresh-access"
    assert route.called

    db_session.expire_all()
    stored = db_session.query(OAuthToken).one()
    assert stored.refresh_token == "refresh-2"


@respx.mock
def test_get_valid_access_token_refreshes_inside_skew_window(
    db_session, yahoo_credentials
):
    """A token expiring in 30s is treated as expired (60s skew)."""
    make_token(db_session, "about-to-expire", "refresh-1", expires_in_seconds=30)
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "fresh-access",
                "refresh_token": "refresh-2",
                "expires_in": 3600,
            },
        )
    )

    assert oauth.get_valid_access_token(db_session) == "fresh-access"
    assert route.called


@respx.mock(assert_all_called=False)
def test_get_valid_access_token_noop_when_fresh(
    respx_mock, db_session, yahoo_credentials
):
    make_token(db_session, "good-access", "refresh-1", expires_in_seconds=3600)
    route = respx_mock.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={}))

    assert oauth.get_valid_access_token(db_session) == "good-access"
    assert not route.called


def test_get_valid_access_token_requires_connection(db_session):
    with pytest.raises(oauth.YahooNotConnectedError):
        oauth.get_valid_access_token(db_session)


def test_refresh_requires_connection(db_session, yahoo_credentials):
    with pytest.raises(oauth.YahooNotConnectedError):
        oauth.refresh_token(db_session)
