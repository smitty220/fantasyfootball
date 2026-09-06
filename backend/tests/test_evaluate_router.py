"""HTTP surface of the evaluation engine."""

from __future__ import annotations

import pytest

from app.services.yahoo.sync import current_nfl_season
from tests.test_evaluator import (
    SEASON,
    SMALL_SLOTS,
    make_league,
    make_player,
    make_team,
    roster,
    set_value,
)


@pytest.fixture()
def seeded(db_session):
    league = make_league(db_session, roster_slots=SMALL_SLOTS, num_teams=2)
    team_a = make_team(db_session, league, "Alpha", is_my_team=True)
    team_b = make_team(db_session, league, "Bravo")

    a_star = make_player(db_session, "A Star RB", "RB", 200)
    a_qb = make_player(db_session, "A QB", "QB", 300)
    roster(db_session, league, team_a, a_star, a_qb)

    b_star = make_player(db_session, "B Star WR", "WR", 190)
    b_qb = make_player(db_session, "B QB", "QB", 280)
    roster(db_session, league, team_b, b_star, b_qb)

    fa_rb = make_player(db_session, "Hot RB", "RB", 100)
    make_player(db_session, "Quiet WR", "WR", 40)

    set_value(db_session, a_star, 5000)
    set_value(db_session, b_star, 500)
    set_value(db_session, fa_rb, 900)
    db_session.commit()

    return league, {"team_a": team_a, "team_b": team_b, "a_star": a_star, "b_star": b_star}


def test_free_agents_endpoint_returns_ranked_rows(client, seeded):
    body = client.get("/api/leagues/manual.1/evaluate/free-agents").json()
    assert body["league_key"] == "manual.1"
    assert body["season"] == current_nfl_season() == SEASON
    assert [row["full_name"] for row in body["rows"]] == ["Hot RB", "Quiet WR"]

    hot = body["rows"][0]
    assert hot["ros_points"] == 100.0
    assert hot["trade_value"] == 900
    assert hot["has_projection"] is True


def test_free_agents_endpoint_position_filter_and_limit(client, seeded):
    body = client.get(
        "/api/leagues/manual.1/evaluate/free-agents?position=wr"
    ).json()
    assert [row["full_name"] for row in body["rows"]] == ["Quiet WR"]

    limited = client.get(
        "/api/leagues/manual.1/evaluate/free-agents?limit=1"
    ).json()
    assert len(limited["rows"]) == 1


def test_free_agents_endpoint_unknown_league_is_404(client, seeded):
    response = client.get("/api/leagues/nope.l.1/evaluate/free-agents")
    assert response.status_code == 404
    assert "nope.l.1" in response.json()["detail"]


def test_trade_endpoint_returns_verdict_and_notes(client, seeded):
    league, f = seeded
    response = client.post(
        "/api/leagues/manual.1/evaluate/trade",
        json={
            "side_a": {"team_id": f["team_a"].id, "player_ids": [f["a_star"].id]},
            "side_b": {"team_id": f["team_b"].id, "player_ids": [f["b_star"].id]},
        },
    )
    assert response.status_code == 200

    body = response.json()
    assert body["verdict"] == "favors_b"
    assert body["margin_pct"] == pytest.approx(0.9)
    assert body["sides"]["a"]["team_name"] == "Alpha"
    assert body["sides"]["b"]["team_name"] == "Bravo"
    assert body["sides"]["a"]["players"][0]["full_name"] == "A Star RB"
    assert any("optimal lineup changes by" in note for note in body["notes"])


def test_trade_endpoint_validation_error_is_400(client, seeded):
    league, f = seeded
    response = client.post(
        "/api/leagues/manual.1/evaluate/trade",
        json={
            "side_a": {"team_id": f["team_a"].id, "player_ids": [f["b_star"].id]},
            "side_b": {"team_id": f["team_b"].id, "player_ids": [f["a_star"].id]},
        },
    )
    assert response.status_code == 400
    assert "not on Alpha" in response.json()["detail"]


def test_trade_endpoint_empty_side_is_400(client, seeded):
    league, f = seeded
    response = client.post(
        "/api/leagues/manual.1/evaluate/trade",
        json={
            "side_a": {"team_id": f["team_a"].id, "player_ids": []},
            "side_b": {"team_id": f["team_b"].id, "player_ids": [f["b_star"].id]},
        },
    )
    assert response.status_code == 400
    assert "at least one player" in response.json()["detail"]


def test_trade_endpoint_unknown_league_is_404(client, seeded):
    league, f = seeded
    response = client.post(
        "/api/leagues/nope.l.1/evaluate/trade",
        json={
            "side_a": {"team_id": f["team_a"].id, "player_ids": [f["a_star"].id]},
            "side_b": {"team_id": f["team_b"].id, "player_ids": [f["b_star"].id]},
        },
    )
    assert response.status_code == 404
