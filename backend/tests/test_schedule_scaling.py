"""Schedule awareness inside the evaluator.

Two things are under test here: rest-of-season points shrinking to the games a
team actually has left, and the ``bye_week``/``opponent``/``on_bye`` fields
every player row now carries. Both are inert until ``nfl_games`` rows exist,
which is what keeps every other test in the suite unchanged.
"""

from __future__ import annotations

import pytest

from app.models import NflGame
from app.services import evaluator, scoring
from tests.test_evaluator import (
    FULL_SLOTS,
    SEASON,
    SMALL_SLOTS,
    make_league,
    make_player,
    make_team,
    roster,
    set_week_points,
)

#: A full season's worth of games, per team.
FULL = evaluator.GAMES_PER_SEASON


def add_game(db, week: int, home: str, away: str, *, season: int = SEASON) -> NflGame:
    game = NflGame(season=season, week=week, home_team=home, away_team=away)
    db.add(game)
    db.flush()
    return game


def give_games(db, team: str, weeks, *, opponent: str = "OPP") -> None:
    """Put ``team`` on the schedule in each of ``weeks`` (always as the home
    side, against a throwaway opponent that differs per week so the
    ``(season, week, home_team)`` unique constraint is never in play)."""
    for week in weeks:
        add_game(db, week, home=team, away=f"{opponent}{week}")


# --- ROS scaling -----------------------------------------------------------


@pytest.fixture()
def scaling_league(db_session):
    """A one-team league whose only player is a 170-point RB on SF.

    170 divides cleanly by 17, so every expected value below is exact.
    """
    league = make_league(db_session, roster_slots=SMALL_SLOTS, num_teams=1)
    team = make_team(db_session, league, "Mine", is_my_team=True)
    player = make_player(db_session, "Scaled RB", "RB", 170, nfl_team="SF")
    roster(db_session, league, team, player)
    db_session.commit()
    return league, team, player


def ros_points(db, league, player) -> float:
    rules = scoring.league_rules(league.settings_json)
    return evaluator._projection_points(db, SEASON, rules, [player.id])[player.id]


def test_no_schedule_means_no_scaling(scaling_league, db_session):
    league, _team, player = scaling_league

    assert ros_points(db_session, league, player) == 170.0


def test_ros_points_scale_by_remaining_games(scaling_league, db_session):
    league, _team, player = scaling_league
    # 8 games left out of 17, starting from week 1 (no weekly projections on
    # file, so the horizon falls back to week 1).
    give_games(db_session, "SF", range(1, 9))
    db_session.commit()

    assert ros_points(db_session, league, player) == pytest.approx(170.0 * 8 / FULL)


def test_scaling_starts_at_the_current_projection_week(scaling_league, db_session):
    league, _team, player = scaling_league
    give_games(db_session, "SF", range(1, 19))
    # Weekly projections describe week 15, so only weeks 15-18 are ahead of us.
    set_week_points(db_session, player, 12.0, week=15)
    db_session.commit()

    assert ros_points(db_session, league, player) == pytest.approx(170.0 * 4 / FULL)


def test_a_full_schedule_leaves_points_untouched(scaling_league, db_session):
    league, _team, player = scaling_league
    # 17 games plus a bye = a whole season ahead.
    give_games(db_session, "SF", [w for w in range(1, 19) if w != 9])
    db_session.commit()

    assert ros_points(db_session, league, player) == pytest.approx(170.0)


def test_unknown_team_is_left_unscaled(db_session):
    league = make_league(db_session, roster_slots=SMALL_SLOTS, num_teams=1)
    stranger = make_player(db_session, "Nowhere RB", "RB", 170, nfl_team="XXX")
    teamless = make_player(db_session, "Teamless RB", "RB", 170, nfl_team=None)
    give_games(db_session, "SF", range(1, 9))
    db_session.commit()

    assert ros_points(db_session, league, stranger) == 170.0
    assert ros_points(db_session, league, teamless) == 170.0


def test_crosswalk_team_spelling_still_scales(db_session):
    """A player whose ``nfl_team`` came from the nflverse crosswalk (``GBP``)
    must match the schedule's ``GB`` -- both sides go through
    ``nfl_schedule.normalize_team``."""
    league = make_league(db_session, roster_slots=SMALL_SLOTS, num_teams=1)
    packer = make_player(db_session, "Packer RB", "RB", 170, nfl_team="GBP")
    give_games(db_session, "GB", range(1, 9))
    db_session.commit()

    assert ros_points(db_session, league, packer) == pytest.approx(170.0 * 8 / FULL)


def test_weekly_points_are_never_scaled(scaling_league, db_session):
    league, _team, player = scaling_league
    give_games(db_session, "SF", range(1, 9))
    set_week_points(db_session, player, 12.0, week=1)
    db_session.commit()

    assert evaluator._week_points(
        db_session, league, [player.id], SEASON, 1
    ) == {player.id: 12.0}


def test_another_seasons_schedule_does_not_scale(scaling_league, db_session):
    league, _team, player = scaling_league
    for week in range(1, 9):
        add_game(db_session, week, home="SF", away=f"OPP{week}", season=SEASON - 1)
    db_session.commit()

    assert ros_points(db_session, league, player) == 170.0


def test_replacement_levels_scale_with_the_pool(db_session):
    """VOR stays meaningful mid-season: the replacement level shrinks by the
    same factor the players measured against it do."""
    league = make_league(db_session, roster_slots=SMALL_SLOTS, num_teams=1)
    for i, points in enumerate((170.0, 85.0, 34.0)):
        make_player(db_session, f"RB{i}", "RB", points, nfl_team="SF")
    db_session.commit()

    unscaled = evaluator.replacement_levels(db_session, league, SEASON)["RB"]

    give_games(db_session, "SF", range(1, 9))
    db_session.commit()
    scaled = evaluator.replacement_levels(db_session, league, SEASON)["RB"]

    # RB demand is 1 + 0.4 of the FLEX = 1.4, so in a one-team league the
    # replacement is the third-best RB.
    assert unscaled == 34.0
    assert scaled == pytest.approx(34.0 * 8 / FULL, abs=0.01)


def test_free_agent_ros_and_vor_use_the_scaled_numbers(db_session):
    league = make_league(db_session, roster_slots=SMALL_SLOTS, num_teams=1)
    make_player(db_session, "Free RB", "RB", 170, nfl_team="SF")
    give_games(db_session, "SF", range(1, 9))
    db_session.commit()

    row = evaluator.evaluate_free_agents(db_session, league, season=SEASON)["rows"][0]

    expected = round(170.0 * 8 / FULL, 2)
    assert row["ros_points"] == pytest.approx(expected, abs=0.01)
    assert row["ppg"] == pytest.approx(round(expected / FULL, 1), abs=0.05)
    # Only player at the position, so it is its own replacement level: VOR 0.
    assert row["vor"] == 0.0


# --- bye / opponent payload fields -----------------------------------------


@pytest.fixture()
def bye_league(db_session):
    """A team with one player on SF (playing week 1) and one on GB (on bye).

    Both have a week-1 projection so the response's current week is 1.
    """
    league = make_league(db_session, roster_slots=FULL_SLOTS, num_teams=2)
    team = make_team(db_session, league, "Mine", is_my_team=True)
    playing = make_player(db_session, "Playing QB", "QB", 170, nfl_team="SF")
    resting = make_player(db_session, "Resting RB", "RB", 170, nfl_team="GB")
    playing.bye_week = 9
    resting.bye_week = 1
    roster(db_session, league, team, playing, resting)
    set_week_points(db_session, playing, 20.0, week=1)
    set_week_points(db_session, resting, 15.0, week=1)

    add_game(db_session, 1, home="SEA", away="SF")
    add_game(db_session, 2, home="GB", away="SF")
    db_session.commit()
    return league, team, playing, resting


def by_name(rows: list[dict]) -> dict[str, dict]:
    return {row["full_name"]: row for row in rows}


def lineup_rows(lineup: dict) -> list[dict]:
    filled = [slot["player"] for slot in lineup["slots"] if slot["player"]]
    return filled + lineup["bench"]


def test_lineup_rows_carry_bye_opponent_and_on_bye(bye_league, db_session):
    league, team, _playing, _resting = bye_league

    rows = by_name(lineup_rows(evaluator.team_lineup(db_session, league, team.id)))

    assert rows["Playing QB"]["bye_week"] == 9
    assert rows["Playing QB"]["opponent"] == "@ SEA"
    assert rows["Playing QB"]["on_bye"] is False

    assert rows["Resting RB"]["bye_week"] == 1
    assert rows["Resting RB"]["opponent"] is None
    assert rows["Resting RB"]["on_bye"] is True


def test_home_and_away_opponents_read_differently(bye_league, db_session):
    league, team, _playing, resting = bye_league
    # Move the current week to 2, where GB hosts SF.
    set_week_points(db_session, resting, 15.0, week=2)
    db_session.commit()

    rows = by_name(lineup_rows(evaluator.team_lineup(db_session, league, team.id)))

    assert rows["Resting RB"]["opponent"] == "vs SF"
    assert rows["Resting RB"]["on_bye"] is False


def test_my_player_and_free_agent_rows_carry_the_same_fields(bye_league, db_session):
    league, _team, _playing, _resting = bye_league
    free_agent = make_player(db_session, "Free WR", "WR", 100, nfl_team="GB")
    free_agent.bye_week = 1
    db_session.commit()

    payload = evaluator.evaluate_free_agents(db_session, league, season=SEASON)

    mine = by_name(payload["my_players"])
    assert mine["Playing QB"]["opponent"] == "@ SEA"
    assert mine["Resting RB"]["on_bye"] is True
    assert mine["Resting RB"]["bye_week"] == 1

    fa = by_name(payload["rows"])["Free WR"]
    assert fa["bye_week"] == 1
    assert fa["opponent"] is None
    assert fa["on_bye"] is True


def test_schedule_fields_are_unknown_without_a_schedule(db_session):
    league = make_league(db_session, roster_slots=FULL_SLOTS, num_teams=2)
    team = make_team(db_session, league, "Mine", is_my_team=True)
    player = make_player(db_session, "Lonely QB", "QB", 170, nfl_team="SF")
    roster(db_session, league, team, player)
    set_week_points(db_session, player, 20.0, week=1)
    db_session.commit()

    row = lineup_rows(evaluator.team_lineup(db_session, league, team.id))[0]

    assert row["bye_week"] is None
    assert row["opponent"] is None
    # Crucially false, not true: no schedule means "we don't know", not "bye".
    assert row["on_bye"] is False


def test_no_current_week_means_no_opponent(bye_league, db_session):
    league, team, _playing, _resting = bye_league
    # Drop every weekly projection, so current_projection_week() is None.
    db_session.query(evaluator.Projection).filter(
        evaluator.Projection.week.isnot(None)
    ).delete()
    db_session.commit()

    rows = by_name(lineup_rows(evaluator.team_lineup(db_session, league, team.id)))

    assert rows["Resting RB"]["opponent"] is None
    assert rows["Resting RB"]["on_bye"] is False
    # The bye week itself still comes off the player row.
    assert rows["Resting RB"]["bye_week"] == 1


def test_remaining_only_rows_are_never_rescaled(db_session):
    """A FantasyPros ros=true row already spans only the remaining games."""
    from app.models import Projection
    from datetime import datetime, timezone

    league = make_league(db_session, roster_slots=SMALL_SLOTS)
    player = make_player(db_session, "ROS Row Guy", "RB", nfl_team="SF")
    # Marked FP row worth 100 pts; unmarked ESPN row worth 100 pts.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db_session.add(Projection(player_id=player.id, source="fantasypros",
                              season=SEASON, week=None,
                              stat_json={"rush_yds": 1000, "_remaining_only": True},
                              fetched_at=now))
    db_session.add(Projection(player_id=player.id, source="espn",
                              season=SEASON, week=None,
                              stat_json={"rush_yds": 1000}, fetched_at=now))
    # SF has 8 of 17 games left.
    give_games(db_session, "SF", range(1, 9))
    db_session.commit()

    rules = scoring.get_preset("half_ppr")
    # FP alone: unscaled (already remaining-only).
    fp = evaluator._projection_points(
        db_session, SEASON, rules, [player.id], sources=("fantasypros",)
    )[player.id]
    assert fp == pytest.approx(100.0)
    # ESPN alone: scaled to 8/17.
    espn = evaluator._projection_points(
        db_session, SEASON, rules, [player.id], sources=("espn",)
    )[player.id]
    assert espn == pytest.approx(100.0 * 8 / FULL)
    # Blend: average of the two, each treated per its own horizon.
    both = evaluator._projection_points(
        db_session, SEASON, rules, [player.id], sources=("fantasypros", "espn")
    )[player.id]
    assert both == pytest.approx((100.0 + 100.0 * 8 / FULL) / 2)
