"""Standings from matchup rows, and the Monte Carlo playoff simulator."""

from __future__ import annotations

import random

import pytest

from app.models import Matchup, Team
from app.services import playoff_odds
from app.services import standings as standings_service
from tests.test_evaluator import (
    SMALL_SLOTS,
    make_league,
    make_player,
    make_team,
    roster,
)


# --- seeding helpers -------------------------------------------------------


def add_matchup(
    db,
    league,
    week: int,
    home: Team,
    away: Team | None,
    home_points: float | None = None,
    away_points: float | None = None,
    *,
    is_playoffs: bool = False,
) -> Matchup:
    final = home_points is not None and away_points is not None
    row = Matchup(
        league_id=league.id,
        week=week,
        home_team_id=home.id,
        away_team_id=away.id if away is not None else None,
        home_points=home_points,
        away_points=away_points,
        is_playoffs=is_playoffs,
        status="final" if final else "scheduled",
    )
    db.add(row)
    db.flush()
    return row


def stock_team(db, league, name: str, points_per_player: float | None) -> Team:
    """A team with a full SMALL_SLOTS lineup of identical players.

    ``points_per_player`` of ``None`` leaves the roster unprojected, i.e. a
    team the simulator models as scoring 0 on average.
    """
    team = make_team(db, league, name)
    players = [
        make_player(db, f"{name} {position}", position, points_per_player)
        for position in ("QB", "RB", "WR", "TE")
    ]
    roster(db, league, team, *players)
    return team


@pytest.fixture()
def four_team_league(db_session):
    league = make_league(db_session, roster_slots=SMALL_SLOTS, num_teams=4)
    teams = [make_team(db_session, league, name) for name in ("A", "B", "C", "D")]
    teams[0].is_my_team = True
    db_session.commit()
    return league, teams


# --- standings -------------------------------------------------------------


def test_standings_counts_wins_losses_ties_and_points(db_session, four_team_league):
    league, (a, b, c, d) = four_team_league
    add_matchup(db_session, league, 1, a, b, 120.0, 100.0)
    add_matchup(db_session, league, 1, c, d, 90.0, 90.0)
    add_matchup(db_session, league, 2, a, c, 110.0, 130.0)
    db_session.commit()

    table = {
        row["name"]: row
        for row in standings_service.league_standings(db_session, league)
    }

    assert table["A"] == {
        "team_id": a.id,
        "name": "A",
        "is_my_team": True,
        "wins": 1,
        "losses": 1,
        "ties": 0,
        "points_for": 230.0,
        "points_against": 230.0,
        "games_played": 2,
    }
    assert (table["C"]["wins"], table["C"]["ties"]) == (1, 1)
    assert table["C"]["points_for"] == 220.0
    assert (table["D"]["wins"], table["D"]["losses"], table["D"]["ties"]) == (0, 0, 1)
    assert table["B"]["games_played"] == 1


def test_standings_sorts_by_wins_then_points_for(db_session, four_team_league):
    league, (a, b, c, d) = four_team_league
    # A and C both finish 1-0; C scored more.
    add_matchup(db_session, league, 1, a, b, 100.0, 90.0)
    add_matchup(db_session, league, 1, c, d, 150.0, 80.0)
    db_session.commit()

    order = [
        row["name"] for row in standings_service.league_standings(db_session, league)
    ]
    assert order == ["C", "A", "B", "D"]


def test_standings_updates_the_team_rows(db_session, four_team_league):
    league, (a, b, c, d) = four_team_league
    add_matchup(db_session, league, 1, a, b, 100.0, 90.0)
    add_matchup(db_session, league, 1, c, d, 150.0, 80.0)
    db_session.commit()

    standings_service.league_standings(db_session, league)

    db_session.refresh(a)
    db_session.refresh(d)
    assert (a.wins, a.losses, a.ties, a.rank) == (1, 0, 0, 2)
    assert (a.points_for, a.points_against) == (100.0, 90.0)
    assert (d.wins, d.losses, d.rank) == (0, 1, 4)
    assert d.points_against == 150.0


def test_standings_ignores_byes_unplayed_games_and_playoffs(
    db_session, four_team_league
):
    league, (a, b, c, d) = four_team_league
    add_matchup(db_session, league, 1, a, b, 100.0, 90.0)
    add_matchup(db_session, league, 2, a, None, 105.0, None)  # bye
    add_matchup(db_session, league, 3, a, c)  # scheduled, no scores
    add_matchup(db_session, league, 4, a, d, 200.0, 10.0, is_playoffs=True)
    db_session.commit()

    table = {
        row["name"]: row
        for row in standings_service.league_standings(db_session, league)
    }
    assert (table["A"]["wins"], table["A"]["games_played"]) == (1, 1)
    assert table["A"]["points_for"] == 100.0
    assert standings_service.completed_weeks(db_session, league) == [1]


def test_standings_without_final_matchups_leaves_team_rows_alone(
    db_session, four_team_league
):
    """A synced league with no matchup rows must keep its synced record."""
    league, (a, _b, _c, _d) = four_team_league
    a.wins, a.losses, a.rank, a.points_for = 7, 3, 2, 1234.5
    db_session.commit()

    table = standings_service.league_standings(db_session, league)
    assert all(row["wins"] == 0 for row in table)

    db_session.refresh(a)
    assert (a.wins, a.losses, a.rank, a.points_for) == (7, 3, 2, 1234.5)


def test_standings_of_a_league_with_no_teams(db_session):
    league = make_league(db_session, roster_slots=SMALL_SLOTS, num_teams=0)
    db_session.commit()
    assert standings_service.league_standings(db_session, league) == []


# --- simulator configuration ----------------------------------------------


def test_league_config_defaults(db_session):
    league = make_league(db_session, roster_slots=SMALL_SLOTS)
    assert playoff_odds.league_config(league) == (14, 6)


def test_league_config_reads_settings(db_session):
    league = make_league(db_session, roster_slots=SMALL_SLOTS)
    league.settings_json = {
        **(league.settings_json or {}),
        "regular_season_weeks": 12,
        "playoff_teams": 4,
    }
    assert playoff_odds.league_config(league) == (12, 4)


def test_league_config_ignores_nonsense(db_session):
    league = make_league(db_session, roster_slots=SMALL_SLOTS)
    league.settings_json = {
        **(league.settings_json or {}),
        "regular_season_weeks": "twelve",
        "playoff_teams": 0,
    }
    assert playoff_odds.league_config(league) == (14, 6)


# --- pairing helpers -------------------------------------------------------


def test_random_pairs_never_pairs_a_team_with_itself():
    rng = random.Random(7)
    for _ in range(50):
        pairs = playoff_odds._random_pairs(list(range(6)), rng)
        assert len(pairs) == 3
        assert all(home != away for home, away in pairs)
        assert sorted(t for pair in pairs for t in pair) == list(range(6))


def test_random_pairs_leaves_one_team_out_when_the_count_is_odd():
    rng = random.Random(7)
    pairs = playoff_odds._random_pairs(list(range(5)), rng)
    assert len(pairs) == 2
    played = [t for pair in pairs for t in pair]
    assert len(set(played)) == 4


def test_entered_pairings_maps_teams_to_indices(db_session, four_team_league):
    league, (a, b, c, d) = four_team_league
    add_matchup(db_session, league, 5, a, b)
    add_matchup(db_session, league, 5, c, None)  # bye: not a game
    db_session.commit()

    index = {team.id: i for i, team in enumerate((a, b, c, d))}
    pairings = playoff_odds._entered_pairings(db_session, league, [5], index)
    assert pairings == {5: [(0, 1)]}


# --- simulator -------------------------------------------------------------


def _simulate(db, league, **kwargs):
    kwargs.setdefault("sims", 2000)
    kwargs.setdefault("rng", random.Random(1234))
    return playoff_odds.simulate(db, league, **kwargs)


def _by_name(result: dict) -> dict[str, dict]:
    return {row["name"]: row for row in result["teams"]}


@pytest.fixture()
def lopsided_league(db_session):
    """Four teams graded from a juggernaut (A) to one that never scores (D)."""
    league = make_league(db_session, roster_slots=SMALL_SLOTS, num_teams=4)
    league.settings_json = {
        **(league.settings_json or {}),
        "regular_season_weeks": 4,
        "playoff_teams": 2,
    }
    teams = {
        "A": stock_team(db_session, league, "A", 850.0),
        "B": stock_team(db_session, league, "B", 300.0),
        "C": stock_team(db_session, league, "C", 150.0),
        "D": stock_team(db_session, league, "D", None),
    }
    db_session.commit()
    return league, teams


def test_dominant_team_always_makes_the_playoffs(db_session, lopsided_league):
    league, _teams = lopsided_league
    result = _simulate(db_session, league)

    assert result["sims"] == 2000
    assert result["regular_season_weeks"] == 4
    assert result["playoff_teams"] == 2
    assert result["completed_weeks"] == []

    rows = _by_name(result)
    assert rows["A"]["playoff_prob"] > 0.99
    assert rows["A"]["seed_1_prob"] > 0.99
    assert rows["A"]["avg_seed"] == pytest.approx(1.0, abs=0.05)
    assert rows["D"]["playoff_prob"] < 0.01
    assert rows["D"]["seed_1_prob"] == 0.0
    assert rows["D"]["avg_seed"] == pytest.approx(4.0, abs=0.05)
    # Best odds first.
    assert [row["name"] for row in result["teams"]] == ["A", "B", "C", "D"]


def test_entered_future_matchups_set_who_plays_whom(db_session):
    """A week with pairings entered uses them instead of pairing at random.

    Two juggernauts (A, D) and two teams that never score (B, C), one week to
    play, two playoff spots. The entered schedule puts the juggernauts against
    each other, so one of B/C wins its game and takes the second seed *every*
    time. Left to random pairing the juggernauts would meet in only a third of
    the sims, and B and C would miss the playoffs in the rest.
    """
    league = make_league(db_session, roster_slots=SMALL_SLOTS, num_teams=4)
    league.settings_json = {
        **(league.settings_json or {}),
        "regular_season_weeks": 1,
        "playoff_teams": 2,
    }
    teams = {
        name: stock_team(db_session, league, name, points)
        for name, points in (("A", 850.0), ("B", None), ("C", None), ("D", 850.0))
    }
    add_matchup(db_session, league, 1, teams["A"], teams["D"])
    add_matchup(db_session, league, 1, teams["B"], teams["C"])
    db_session.commit()

    rows = _by_name(_simulate(db_session, league))
    assert rows["B"]["playoff_prob"] + rows["C"]["playoff_prob"] == pytest.approx(
        1.0, abs=0.01
    )
    assert rows["B"]["playoff_prob"] > 0.4
    assert rows["A"]["playoff_prob"] + rows["D"]["playoff_prob"] == pytest.approx(
        1.0, abs=0.01
    )
    # A juggernaut always outscores a team that never scores, so the winner of
    # the juggernauts' game is always the 1 seed.
    assert rows["A"]["seed_1_prob"] + rows["D"]["seed_1_prob"] == pytest.approx(
        1.0, abs=0.01
    )


def test_completed_weeks_are_not_resimulated(db_session, lopsided_league):
    """Week 1 already happened: the juggernauts lost it, and that is final."""
    league, teams = lopsided_league
    league.settings_json = {
        **(league.settings_json or {}),
        "regular_season_weeks": 1,
        "playoff_teams": 2,
    }
    add_matchup(db_session, league, 1, teams["B"], teams["A"], 90.0, 80.0)
    add_matchup(db_session, league, 1, teams["C"], teams["D"], 70.0, 60.0)
    db_session.commit()

    result = _simulate(db_session, league)
    assert result["completed_weeks"] == [1]

    rows = _by_name(result)
    assert rows["B"]["current"] == {
        "wins": 1,
        "losses": 0,
        "ties": 0,
        "points_for": 90.0,
    }
    assert rows["B"]["playoff_prob"] == 1.0
    assert rows["C"]["playoff_prob"] == 1.0
    assert rows["A"]["playoff_prob"] == 0.0
    assert rows["D"]["playoff_prob"] == 0.0


def test_simulation_is_reproducible_with_a_seeded_rng(db_session, lopsided_league):
    league, _teams = lopsided_league
    first = playoff_odds.simulate(db_session, league, sims=200, rng=random.Random(5))
    second = playoff_odds.simulate(db_session, league, sims=200, rng=random.Random(5))
    assert first == second

    third = playoff_odds.simulate(db_session, league, sims=200, rng=random.Random(6))
    assert third != first


def test_simulate_on_a_league_with_no_teams(db_session):
    league = make_league(db_session, roster_slots=SMALL_SLOTS, num_teams=0)
    db_session.commit()
    result = playoff_odds.simulate(db_session, league, sims=10)
    assert result["teams"] == []
    assert result["completed_weeks"] == []
    assert result["playoff_teams"] == 6


def test_every_team_makes_a_playoff_field_bigger_than_the_league(
    db_session, four_team_league
):
    league, _teams = four_team_league  # 4 teams, default 6-team playoff field
    rows = _by_name(
        playoff_odds.simulate(db_session, league, sims=50, rng=random.Random(3))
    )
    assert all(row["playoff_prob"] == 1.0 for row in rows.values())


# --- endpoints -------------------------------------------------------------


def test_standings_endpoint(client, db_session, four_team_league):
    league, (a, b, c, d) = four_team_league
    add_matchup(db_session, league, 1, a, b, 100.0, 90.0)
    add_matchup(db_session, league, 1, c, d, 150.0, 80.0)
    db_session.commit()

    response = client.get(f"/api/leagues/{league.league_key}/standings")
    assert response.status_code == 200
    body = response.json()
    assert [row["name"] for row in body] == ["C", "A", "B", "D"]
    assert body[0] == {
        "team_id": c.id,
        "name": "C",
        "is_my_team": False,
        "wins": 1,
        "losses": 0,
        "ties": 0,
        "points_for": 150.0,
        "points_against": 80.0,
        "games_played": 1,
    }

    # The teams endpoint now shows the same record.
    teams = client.get(f"/api/leagues/{league.league_key}/teams").json()
    assert teams[0]["name"] == "C"
    assert teams[0]["rank"] == 1


def test_playoff_odds_endpoint(client, db_session, four_team_league):
    league, (a, b, _c, _d) = four_team_league
    add_matchup(db_session, league, 1, a, b, 100.0, 90.0)
    db_session.commit()

    response = client.get(
        f"/api/leagues/{league.league_key}/playoff-odds", params={"sims": 100}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["league_key"] == league.league_key
    assert body["sims"] == 100
    assert body["regular_season_weeks"] == 14
    assert body["playoff_teams"] == 6
    assert body["completed_weeks"] == [1]
    assert len(body["teams"]) == 4
    row = body["teams"][0]
    assert set(row) == {
        "team_id",
        "name",
        "is_my_team",
        "current",
        "playoff_prob",
        "avg_seed",
        "seed_1_prob",
    }
    assert set(row["current"]) == {"wins", "losses", "ties", "points_for"}


def test_standings_endpoints_404_on_an_unknown_league(client):
    assert client.get("/api/leagues/nope.1/standings").status_code == 404
    assert client.get("/api/leagues/nope.1/playoff-odds").status_code == 404


def test_playoff_odds_endpoint_on_an_empty_league(client, db_session):
    league = make_league(db_session, roster_slots=SMALL_SLOTS, num_teams=0)
    db_session.commit()
    response = client.get(
        f"/api/leagues/{league.league_key}/playoff-odds", params={"sims": 10}
    )
    assert response.status_code == 200
    assert response.json()["teams"] == []
    assert client.get(f"/api/leagues/{league.league_key}/standings").json() == []


def test_playoff_odds_endpoint_caps_sims(client, db_session, four_team_league):
    league, _teams = four_team_league
    response = client.get(
        f"/api/leagues/{league.league_key}/playoff-odds", params={"sims": 20001}
    )
    assert response.status_code == 422


def test_playoff_odds_endpoint_rejects_an_unknown_source(
    client, db_session, four_team_league
):
    league, _teams = four_team_league
    response = client.get(
        f"/api/leagues/{league.league_key}/playoff-odds",
        params={"sims": 10, "sources": "nonsense"},
    )
    assert response.status_code == 400
