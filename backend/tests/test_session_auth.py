"""The app password gate: token signing, login, and request enforcement."""

from __future__ import annotations

import pytest

from app.config import settings
from app.services import session_auth
from tests.test_evaluator import SMALL_SLOTS, make_league, make_player, make_team, roster

OWNER_PW = "owner-secret"
LEAGUE_PW = "league-secret"


@pytest.fixture()
def auth_on(monkeypatch):
    monkeypatch.setattr(settings, "OWNER_PASSWORD", OWNER_PW)
    monkeypatch.setattr(settings, "LEAGUE_PASSWORD", LEAGUE_PW)
    monkeypatch.setattr(settings, "SESSION_SECRET", "unit-test-signing-key")


@pytest.fixture()
def tradeable(db_session):
    """A two-team league the trade evaluator can actually answer about."""
    league = make_league(db_session, roster_slots=SMALL_SLOTS, num_teams=2)
    team_a = make_team(db_session, league, "Alpha", is_my_team=True)
    team_b = make_team(db_session, league, "Bravo")

    a_rb = make_player(db_session, "A RB", "RB", 200)
    roster(db_session, league, team_a, a_rb, make_player(db_session, "A QB", "QB", 300))
    b_wr = make_player(db_session, "B WR", "WR", 190)
    roster(db_session, league, team_b, b_wr, make_player(db_session, "B QB", "QB", 280))
    db_session.commit()

    return {
        "side_a": {"team_id": team_a.id, "player_ids": [a_rb.id]},
        "side_b": {"team_id": team_b.id, "player_ids": [b_wr.id]},
    }


def login(client, password: str):
    return client.post("/api/session/login", json={"password": password})


def new_league_body(name: str = "Gate League"):
    return {"name": name, "season": 2026}


# --- token unit tests ------------------------------------------------------


def test_round_trips_a_signed_token(auth_on):
    assert session_auth.verify_token(session_auth.create_token("owner")) == "owner"
    assert session_auth.verify_token(session_auth.create_token("viewer")) == "viewer"


def test_rejects_tampered_expired_and_malformed_tokens(auth_on):
    token = session_auth.create_token("viewer")
    role, expiry, signature = token.split(".")

    # Same signature, escalated role.
    assert session_auth.verify_token(f"owner.{expiry}.{signature}") is None
    # Same signature, extended expiry.
    assert session_auth.verify_token(f"{role}.{int(expiry) + 999}.{signature}") is None
    assert session_auth.verify_token(f"{role}.{expiry}.{'0' * len(signature)}") is None
    assert session_auth.verify_token("not-a-token") is None
    assert session_auth.verify_token("") is None
    assert session_auth.verify_token(None) is None
    assert session_auth.verify_token(session_auth.create_token("owner", -1)) is None


def test_token_signed_with_another_secret_is_rejected(auth_on, monkeypatch):
    token = session_auth.create_token("owner")
    monkeypatch.setattr(settings, "SESSION_SECRET", "a-different-key")
    assert session_auth.verify_token(token) is None


# --- gate disabled (the default everything else in the suite runs under) ---


def test_auth_disabled_leaves_every_endpoint_open(client):
    assert session_auth.auth_enabled() is False
    assert client.get("/api/leagues").status_code == 200
    assert client.post("/api/manual/leagues", json=new_league_body()).status_code == 201


def test_me_reports_auth_disabled(client):
    assert client.get("/api/session/me").json() == {"auth_enabled": False, "role": None}


def test_empty_password_never_matches_when_disabled(client):
    assert login(client, "").status_code == 401


# --- gate enabled ----------------------------------------------------------


def test_unauthenticated_api_request_is_401(client, auth_on):
    response = client.get("/api/leagues")
    assert response.status_code == 401
    assert "Sign in" in response.json()["detail"]


def test_health_and_session_endpoints_stay_open(client, auth_on):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/session/me").json() == {"auth_enabled": True, "role": None}


def test_login_with_owner_password(client, auth_on):
    response = login(client, OWNER_PW)
    assert response.status_code == 200
    assert response.json() == {"role": "owner"}

    cookie = response.cookies[session_auth.COOKIE_NAME]
    assert session_auth.verify_token(cookie) == "owner"
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()

    assert client.get("/api/session/me").json() == {
        "auth_enabled": True,
        "role": "owner",
    }


def test_login_with_league_password_yields_viewer(client, auth_on):
    assert login(client, LEAGUE_PW).json() == {"role": "viewer"}
    assert client.get("/api/session/me").json()["role"] == "viewer"


def test_login_with_wrong_password_is_401(client, auth_on):
    assert login(client, "nope").status_code == 401
    assert client.get("/api/session/me").json()["role"] is None


def test_logout_clears_the_session(client, auth_on):
    login(client, OWNER_PW)
    assert client.post("/api/session/logout").status_code == 200
    assert client.get("/api/session/me").json()["role"] is None
    assert client.get("/api/leagues").status_code == 401


def test_tampered_cookie_is_rejected(client, auth_on):
    login(client, LEAGUE_PW)
    token = client.cookies[session_auth.COOKIE_NAME]
    _, expiry, signature = token.split(".")
    client.cookies.set(session_auth.COOKIE_NAME, f"owner.{expiry}.{signature}")

    assert client.get("/api/leagues").status_code == 401
    assert client.get("/api/session/me").json()["role"] is None


def test_viewer_can_read_but_not_mutate(client, auth_on):
    login(client, LEAGUE_PW)
    assert client.get("/api/leagues").status_code == 200

    response = client.post("/api/manual/leagues", json=new_league_body())
    assert response.status_code == 403
    assert "owner" in response.json()["detail"]


def test_viewer_may_evaluate_trades(client, auth_on, tradeable):
    login(client, LEAGUE_PW)
    response = client.post("/api/leagues/manual.1/evaluate/trade", json=tradeable)
    assert response.status_code == 200
    assert "verdict" in response.json()


def test_owner_may_mutate(client, auth_on):
    login(client, OWNER_PW)
    assert client.post("/api/manual/leagues", json=new_league_body()).status_code == 201


def test_non_api_paths_are_never_gated(client, auth_on):
    # The SPA (or its 404 when no build is mounted) must load so it can render
    # the login screen; what matters is that the gate did not answer 401.
    assert client.get("/").status_code != 401
