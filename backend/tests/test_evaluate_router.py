"""HTTP surface of the evaluation engine."""

from __future__ import annotations

import pytest

from app.models import Player
from app.services.yahoo.sync import current_nfl_season
from tests.test_evaluator import (
    SEASON,
    SMALL_SLOTS,
    make_league,
    make_player,
    make_team,
    roster,
    set_value,
    set_week_points,
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


def test_endpoints_serialize_the_schedule_fields(client, seeded, db_session):
    """The response models carry bye/opponent/on_bye on every player row.

    Seeded with a week-1 game for SF (every ``make_player`` default) and a
    bye_week on one player, so the fields have something real to report.
    """
    from app.models import NflGame

    league, seed = seeded
    db_session.add(NflGame(season=SEASON, week=1, home_team="SEA", away_team="SF"))
    seed["a_star"].bye_week = 9
    set_week_points(db_session, seed["a_star"], 15.0, week=1)
    db_session.commit()

    team_id = seed["team_a"].id
    lineup = client.get(
        f"/api/leagues/manual.1/evaluate/teams/{team_id}/lineup"
    ).json()
    starter = next(slot["player"] for slot in lineup["slots"] if slot["player"])
    assert starter["opponent"] == "@ SEA"
    assert starter["on_bye"] is False

    body = client.get("/api/leagues/manual.1/evaluate/free-agents").json()
    for row in body["rows"] + body["my_players"]:
        assert set(row) >= {"bye_week", "opponent", "on_bye"}
    mine = {row["full_name"]: row for row in body["my_players"]}
    assert mine["A Star RB"]["bye_week"] == 9
    assert mine["A Star RB"]["opponent"] == "@ SEA"


def test_free_agents_endpoint_position_filter_and_limit(client, seeded):
    body = client.get(
        "/api/leagues/manual.1/evaluate/free-agents?position=wr"
    ).json()
    assert [row["full_name"] for row in body["rows"]] == ["Quiet WR"]

    limited = client.get(
        "/api/leagues/manual.1/evaluate/free-agents?limit=1"
    ).json()
    assert len(limited["rows"]) == 1


def test_free_agents_endpoint_exposes_week_points_and_my_players(
    client, seeded, db_session
):
    league, f = seeded
    def player(name: str) -> Player:
        return db_session.query(Player).filter(Player.full_name == name).one()

    set_week_points(db_session, f["a_star"], 15)
    set_week_points(db_session, player("A QB"), 20)
    set_week_points(db_session, player("Hot RB"), 8)
    db_session.commit()

    body = client.get("/api/leagues/manual.1/evaluate/free-agents").json()
    assert body["week"] == 1

    rows = {row["full_name"]: row for row in body["rows"]}
    assert rows["Hot RB"]["week_points"] == 8.0
    # Worst FLEX-eligible starter this week is the 15-point A Star RB.
    assert rows["Hot RB"]["week_delta"] == -7.0
    assert rows["Quiet WR"]["week_points"] is None
    assert rows["Quiet WR"]["week_delta"] is None

    assert [
        (row["starter_slot"], row["full_name"]) for row in body["my_players"]
    ] == [("QB", "A QB"), ("RB", "A Star RB")]
    assert all(row["is_starter"] for row in body["my_players"])


def test_free_agents_endpoint_flex_position_filter(client, seeded, db_session):
    make_player(db_session, "Spare QB", "QB", 150)
    db_session.commit()

    plain = client.get("/api/leagues/manual.1/evaluate/free-agents").json()
    assert "Spare QB" in {row["full_name"] for row in plain["rows"]}

    for query in ("FLEX", "W%2FR%2FT"):
        body = client.get(
            f"/api/leagues/manual.1/evaluate/free-agents?position={query}"
        ).json()
        assert {row["full_name"] for row in body["rows"]} == {"Hot RB", "Quiet WR"}


def test_free_agents_endpoint_week_is_null_without_weekly_projections(client, seeded):
    body = client.get("/api/leagues/manual.1/evaluate/free-agents").json()
    assert body["week"] is None
    assert all(row["week_points"] is None for row in body["rows"])


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


# --- lineup ----------------------------------------------------------------


def _seats(body: dict) -> list[tuple[str, str | None]]:
    return [
        (entry["slot"], entry["player"]["full_name"] if entry["player"] else None)
        for entry in body["slots"]
    ]


def test_lineup_endpoint_returns_slots_and_bench(client, seeded, db_session):
    league, f = seeded
    bench_rb = make_player(db_session, "A Bench RB", "RB", 40)
    roster(db_session, league, f["team_a"], bench_rb)
    set_week_points(db_session, f["a_star"], 15)
    db_session.commit()

    body = client.get(
        f"/api/leagues/manual.1/evaluate/teams/{f['team_a'].id}/lineup"
    ).json()

    assert body["league_key"] == "manual.1"
    assert body["team_id"] == f["team_a"].id
    assert body["week"] == 1
    assert body["source"] == "auto"
    assert _seats(body) == [
        ("QB", "A QB"),
        ("RB", "A Star RB"),
        ("WR", None),
        ("FLEX", "A Bench RB"),
    ]
    assert body["slots"][1]["player"]["week_points"] == 15.0
    assert body["slots"][1]["player"]["ros_points"] == 200.0
    assert body["bench"] == []


def test_lineup_endpoint_works_for_a_rival_team(client, seeded):
    league, f = seeded
    body = client.get(
        f"/api/leagues/manual.1/evaluate/teams/{f['team_b'].id}/lineup"
    ).json()
    assert _seats(body) == [
        ("QB", "B QB"),
        ("RB", None),
        ("WR", "B Star WR"),
        ("FLEX", None),
    ]
    assert body["week"] is None


def test_lineup_endpoint_reports_a_saved_lineup(client, seeded, db_session):
    league, f = seeded
    bench_rb = make_player(db_session, "A Bench RB", "RB", 40)
    roster(db_session, league, f["team_a"], bench_rb)
    db_session.commit()

    client.put(
        f"/api/manual/teams/{f['team_a'].id}/lineup",
        json={"assignments": [{"player_id": bench_rb.id, "slot": "RB"}]},
    )

    body = client.get(
        f"/api/leagues/manual.1/evaluate/teams/{f['team_a'].id}/lineup"
    ).json()
    assert body["source"] == "manual"
    assert _seats(body) == [
        ("QB", None),
        ("RB", "A Bench RB"),
        ("WR", None),
        ("FLEX", None),
    ]
    assert [row["full_name"] for row in body["bench"]] == ["A QB", "A Star RB"]


def test_lineup_endpoint_unknown_league_is_404(client, seeded):
    league, f = seeded
    response = client.get(
        f"/api/leagues/nope.l.1/evaluate/teams/{f['team_a'].id}/lineup"
    )
    assert response.status_code == 404
    assert "nope.l.1" in response.json()["detail"]


def test_lineup_endpoint_unknown_team_is_404(client, seeded):
    response = client.get("/api/leagues/manual.1/evaluate/teams/9999/lineup")
    assert response.status_code == 404
    assert "9999" in response.json()["detail"]


def test_lineup_endpoint_rejects_a_team_from_another_league(client, seeded, db_session):
    other = make_league(
        db_session, roster_slots=SMALL_SLOTS, num_teams=2, league_key="manual.2"
    )
    outsider = make_team(db_session, other, "Outsider")
    db_session.commit()

    response = client.get(
        f"/api/leagues/manual.1/evaluate/teams/{outsider.id}/lineup"
    )
    assert response.status_code == 404
