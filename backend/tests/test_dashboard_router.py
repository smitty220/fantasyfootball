"""HTTP surface of the cross-league dashboard endpoint."""

from __future__ import annotations

import pytest

from app.models import NflGame
from tests.test_evaluator import (
    SEASON,
    SMALL_SLOTS,
    make_league,
    make_player,
    make_team,
    roster,
    set_week_points,
)


@pytest.fixture()
def dashboard_seed(db_session):
    """One league with a full-team scenario, plus two my-team-less leagues.

    League "manual.1" (mine): a lineup with a bye starter (RB One), an
    injured starter (WR One), a FLEX starter (RB Two) that a bench player
    outscores this week, and a free-agent QB that beats my own QB on both
    week and ROS points. A handful of extra free agents give the top-VOR
    list more than three projected candidates to truncate.

    "manual.2" has teams but none marked mine; "manual.3" has no teams at
    all. Both exercise the ``my_team is None`` branch.
    """
    league = make_league(db_session, roster_slots=SMALL_SLOTS, num_teams=2, league_key="manual.1")
    my_team = make_team(db_session, league, "My Team", is_my_team=True)

    # A week-1 game between two teams that are *not* my bye player's team, so
    # every other rostered/free-agent player (all on "SEA") is off the bye
    # and RB One (on "SF") reads back as on_bye.
    db_session.add(NflGame(season=SEASON, week=1, home_team="SEA", away_team="GB"))

    qb1 = make_player(db_session, "QB One", "QB", 300, nfl_team="SEA")
    rb1 = make_player(db_session, "RB One", "RB", 200, nfl_team="SF")
    rb2 = make_player(db_session, "RB Two", "RB", 150, nfl_team="SEA")
    wr1 = make_player(db_session, "WR One", "WR", 190, nfl_team="SEA")
    bench_rb = make_player(db_session, "Bench Racer", "RB", 50, nfl_team="SEA")
    bench_wr = make_player(db_session, "Bench Filler", "WR", 20, nfl_team="SEA")
    roster(db_session, league, my_team, qb1, rb1, rb2, wr1, bench_rb, bench_wr)

    wr1.injury_status = "Q"

    set_week_points(db_session, qb1, 25)
    set_week_points(db_session, rb1, 20)
    set_week_points(db_session, rb2, 5)
    set_week_points(db_session, wr1, 18)
    set_week_points(db_session, bench_rb, 30)  # beats RB Two's FLEX week points
    set_week_points(db_session, bench_wr, 3)

    fa_qb = make_player(db_session, "FA Quarterback", "QB", 400, nfl_team="SEA")
    set_week_points(db_session, fa_qb, 40)
    make_player(db_session, "FA WR Alpha", "WR", 130, nfl_team="SEA")
    make_player(db_session, "FA WR Bravo", "WR", 115, nfl_team="SEA")
    make_player(db_session, "FA WR Charlie", "WR", 95, nfl_team="SEA")
    make_player(db_session, "FA RB Delta", "RB", 80, nfl_team="SEA")

    other = make_league(db_session, roster_slots=SMALL_SLOTS, num_teams=2, league_key="manual.2")
    make_team(db_session, other, "Team X")
    make_team(db_session, other, "Team Y")

    make_league(db_session, roster_slots=SMALL_SLOTS, num_teams=2, league_key="manual.3")

    db_session.commit()
    return {"league": league, "my_team": my_team}


def test_dashboard_empty_db_returns_empty_leagues(client):
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    assert response.json() == {"leagues": []}


def test_dashboard_sources_validation_400(client):
    response = client.get("/api/dashboard?sources=bogus")
    assert response.status_code == 400
    assert "bogus" in response.json()["detail"]


def test_dashboard_full_shape(client, dashboard_seed):
    body = client.get("/api/dashboard").json()
    leagues = body["leagues"]
    assert [row["league_key"] for row in leagues] == ["manual.1", "manual.2", "manual.3"]

    mine = leagues[0]
    my_team = dashboard_seed["my_team"]
    assert mine["is_keeper"] is False
    assert mine["week"] == 1
    assert mine["my_team"] == {"id": my_team.id, "name": "My Team"}

    alerts = mine["alerts"]
    assert alerts["bye_starters"] == [{"name": "RB One", "slot": "RB"}]
    assert alerts["injured_starters"] == [
        {"name": "WR One", "slot": "WR", "status": "Q"}
    ]
    # Bench Racer (30 week points, RB) outscores both RB-eligible starters:
    # RB One in the direct RB slot and RB Two in FLEX.
    assert alerts["bench_beats_starter"] == [
        {
            "starter": "RB One",
            "slot": "RB",
            "bench": "Bench Racer",
            "bench_week_points": 30.0,
        },
        {
            "starter": "RB Two",
            "slot": "FLEX",
            "bench": "Bench Racer",
            "bench_week_points": 30.0,
        },
    ]
    assert alerts["fa_week_flags"] == 1
    # ROS-flagged: QB One (beaten by FA Quarterback) plus both bench players,
    # whose low ROS points sit below the best free agent at their position.
    assert alerts["fa_ros_flags"] == 3

    # QB One's starter week (25) + RB One (20) + WR One (18) + RB Two (5).
    assert mine["my_lineup_week_points"] == 68.0

    fa_ground_truth = client.get("/api/leagues/manual.1/evaluate/free-agents").json()
    expected_top = [
        {
            "name": row["full_name"],
            "position": row["position"],
            "vor": row["vor"],
            "week_delta": row["week_delta"],
            "trending_add": row["trending_add"],
        }
        for row in fa_ground_truth["rows"]
        if row["has_projection"]
    ][:3]
    assert len(expected_top) == 3  # the seed has more than 3 projected FAs
    assert mine["top_free_agents"] == expected_top

    for entry in leagues[1:]:
        assert entry["my_team"] is None
        assert entry["alerts"] is None
        assert entry["top_free_agents"] == []
        assert entry["my_lineup_week_points"] is None
        # The projection week is global to the season, not per-league.
        assert entry["week"] == 1
