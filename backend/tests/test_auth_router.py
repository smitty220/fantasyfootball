"""Yahoo auth router happy paths (Yahoo HTTP mocked)."""

from __future__ import annotations

import httpx
import respx

from app.models import OAuthToken
from app.services.yahoo import oauth
from tests.conftest import make_token


def test_status_when_not_connected(client):
    response = client.get("/api/auth/yahoo/status")
    assert response.status_code == 200
    assert response.json() == {"connected": False, "expires_at": None}


def test_status_when_connected(client, db_session):
    token = make_token(db_session, "access-1", "refresh-1")

    body = client.get("/api/auth/yahoo/status").json()
    assert body["connected"] is True
    assert body["expires_at"].startswith(token.expires_at.isoformat()[:19])


def test_start_returns_authorize_url(client, yahoo_credentials):
    body = client.get("/api/auth/yahoo/start").json()
    assert body["authorize_url"].startswith(
        "https://api.login.yahoo.com/oauth2/request_auth?"
    )
    assert "redirect_uri=" in body["authorize_url"]
    assert "client_id=test-client-id" in body["authorize_url"]


def test_start_without_credentials(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "YAHOO_CLIENT_ID", "")
    monkeypatch.setattr(settings, "YAHOO_CLIENT_SECRET", "")

    assert client.get("/api/auth/yahoo/start").status_code == 503


@respx.mock
def test_callback_exchanges_and_stores(client, db_session, yahoo_credentials):
    respx.post(oauth.TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "access-abc",
                "refresh_token": "refresh-abc",
                "expires_in": 3600,
            },
        )
    )

    response = client.post("/api/auth/yahoo/callback", json={"code": "abc123"})
    assert response.status_code == 200
    assert response.json()["connected"] is True

    stored = db_session.query(OAuthToken).one()
    assert stored.access_token == "access-abc"
    assert stored.refresh_token == "refresh-abc"

    # And the status endpoint now agrees.
    assert client.get("/api/auth/yahoo/status").json()["connected"] is True


@respx.mock
def test_callback_rejects_bad_code(client, db_session, yahoo_credentials):
    respx.post(oauth.TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )

    response = client.post("/api/auth/yahoo/callback", json={"code": "nope"})
    assert response.status_code == 400
    assert db_session.query(OAuthToken).count() == 0


def test_callback_rejects_blank_code(client, yahoo_credentials):
    assert (
        client.post("/api/auth/yahoo/callback", json={"code": "  "}).status_code == 422
    )
