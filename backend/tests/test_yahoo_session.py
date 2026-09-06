"""Proves ``yahoo_fantasy_api`` runs on our session adapter.

Yahoo's endpoints are mocked with respx, so this exercises the real library
code paths (Game -> YHandler -> sc.session.get) without touching the network.
"""

from __future__ import annotations

import httpx
import pytest
import respx
import yahoo_fantasy_api as yfa

from app.services.yahoo import oauth
from app.services.yahoo.session import YahooSessionContext, make_session_context
from tests.conftest import make_token, utcnow

YAHOO_API = "https://fantasysports.yahooapis.com/fantasy/v2"

LEAGUES_RESPONSE = {
    "fantasy_content": {
        "users": {
            "0": {
                "user": [
                    {"guid": "ABC123"},
                    {
                        "games": {
                            "0": {
                                "game": [
                                    {"game_key": "461", "code": "nfl"},
                                    {
                                        "leagues": {
                                            "0": {
                                                "league": [
                                                    {
                                                        "league_key": "461.l.12345",
                                                        "name": "Test League",
                                                    }
                                                ]
                                            },
                                            "count": 1,
                                        }
                                    },
                                ]
                            },
                            "count": 1,
                        }
                    },
                ]
            },
            "count": 1,
        }
    }
}


def test_session_context_sends_bearer_token(db_session, yahoo_credentials):
    make_token(db_session, "access-1", "refresh-1", expires_in_seconds=3600)

    with respx.mock:
        route = respx.get(f"{YAHOO_API}/game/nfl").mock(
            return_value=httpx.Response(200, json={"fantasy_content": {}})
        )
        sc = YahooSessionContext(db_session)
        try:
            response = sc.session.get(f"{YAHOO_API}/game/nfl", params={"format": "json"})
        finally:
            sc.close()

    assert response.status_code == 200
    assert route.calls.last.request.headers["authorization"] == "Bearer access-1"
    assert route.calls.last.request.url.params["format"] == "json"


def test_library_game_uses_our_session(db_session, yahoo_credentials):
    make_token(db_session, "access-1", "refresh-1", expires_in_seconds=3600)

    with respx.mock:
        route = respx.get(url__startswith=f"{YAHOO_API}/users/games/leagues").mock(
            return_value=httpx.Response(200, json=LEAGUES_RESPONSE)
        )

        sc = YahooSessionContext(db_session)
        try:
            game = yfa.Game(sc, "nfl")
            league_keys = game.league_ids(game_codes=["nfl"], seasons=["2026"])
        finally:
            sc.close()

    assert league_keys == ["461.l.12345"]
    assert route.calls.last.request.headers["authorization"] == "Bearer access-1"


def test_session_refreshes_expired_token_before_request(db_session, yahoo_credentials):
    token = make_token(db_session, "stale", "refresh-1")
    token.expires_at = utcnow().replace(year=utcnow().year - 1)
    db_session.commit()

    with respx.mock:
        respx.post(oauth.TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "fresh",
                    "refresh_token": "refresh-2",
                    "expires_in": 3600,
                },
            )
        )
        api = respx.get(f"{YAHOO_API}/game/nfl").mock(
            return_value=httpx.Response(200, json={"fantasy_content": {}})
        )

        sc = YahooSessionContext(db_session)
        try:
            sc.session.get(f"{YAHOO_API}/game/nfl")
        finally:
            sc.close()

    assert api.calls.last.request.headers["authorization"] == "Bearer fresh"
    db_session.expire_all()
    assert oauth.get_token(db_session).refresh_token == "refresh-2"


def test_session_setter_is_tolerated(db_session, yahoo_credentials):
    """YHandler reassigns ``sc.session`` after a refresh; that must not blow up."""
    make_token(db_session, "access-1", "refresh-1")
    sc = YahooSessionContext(db_session)
    try:
        original = sc.session
        sc.session = sc.oauth.get_session(token="whatever")
        assert sc.session is original
        sc.session = object()  # ignored, not an error
        assert sc.session is original
    finally:
        sc.close()


def test_refresh_access_token_shim(db_session, yahoo_credentials):
    make_token(db_session, "access-1", "refresh-1")

    with respx.mock:
        respx.post(oauth.TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "access-2",
                    "refresh_token": "refresh-2",
                    "expires_in": 3600,
                },
            )
        )
        sc = YahooSessionContext(db_session)
        try:
            credentials = sc.refresh_access_token()
        finally:
            sc.close()

    assert credentials == {"access_token": "access-2"}
    assert sc.access_token == "access-2"


def test_make_session_context_requires_connection(db_session):
    with pytest.raises(oauth.YahooNotConnectedError):
        make_session_context(db_session)
