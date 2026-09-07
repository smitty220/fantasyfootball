"""Evaluation engine tests: replacement level, VOR, lineups, trades."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models import (
    League,
    LeaguePlayer,
    Player,
    Projection,
    RosterSlot,
    Team,
    TradeValue,
    TrendingSignal,
)
from app.services import evaluator, scoring
from app.services.yahoo.sync import current_nfl_season

SEASON = current_nfl_season()

FULL_SLOTS = {
    "QB": 1,
    "RB": 2,
    "WR": 2,
    "TE": 1,
    "FLEX": 1,
    "K": 1,
    "DEF": 1,
    "BN": 3,
}

SMALL_SLOTS = {"QB": 1, "RB": 1, "WR": 1, "FLEX": 1, "BN": 2}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- seeding helpers -------------------------------------------------------


def make_league(
    db,
    *,
    roster_slots: dict,
    num_teams: int = 2,
    is_keeper: bool = False,
    source: str = "manual",
    league_key: str = "manual.1",
    preset: str = "half_ppr",
) -> League:
    league = League(
        league_key=league_key,
        game_key="manual",
        name="Test League",
        season=SEASON,
        is_keeper=is_keeper,
        num_teams=num_teams,
        scoring_type=preset,
        source=source,
        settings_json={
            "manual": source == "manual",
            "scoring_rules": scoring.get_preset(preset),
            "roster_slots": roster_slots,
        },
    )
    db.add(league)
    db.flush()
    return league


def make_team(db, league: League, name: str, *, is_my_team: bool = False) -> Team:
    team = Team(
        team_key=f"{league.league_key}.t.{name}",
        league_id=league.id,
        name=name,
        is_my_team=is_my_team,
    )
    db.add(team)
    db.flush()
    return team


def make_player(
    db,
    name: str,
    position: str,
    points: float | None = None,
    *,
    source: str = "espn",
    nfl_team: str = "SF",
) -> Player:
    """A player whose season projection scores exactly ``points`` in half-PPR.

    ``rush_yds`` is worth 0.1 in every preset, so the stat line is trivially
    invertible regardless of the player's position.
    """
    player = Player(full_name=name, position=position, nfl_team=nfl_team)
    db.add(player)
    db.flush()
    if points is not None:
        db.add(
            Projection(
                player_id=player.id,
                source=source,
                season=SEASON,
                week=None,
                stat_json={"rush_yds": points * 10},
                fetched_at=_utcnow(),
            )
        )
    return player


def set_week_points(
    db,
    player: Player,
    points: float,
    *,
    week: int = 1,
    source: str = "espn",
) -> None:
    """A single-week projection worth exactly ``points`` in half-PPR."""
    db.add(
        Projection(
            player_id=player.id,
            source=source,
            season=SEASON,
            week=week,
            stat_json={"rush_yds": points * 10},
            fetched_at=_utcnow(),
        )
    )


def roster(db, league: League, team: Team, *players: Player) -> None:
    for player in players:
        db.add(
            LeaguePlayer(
                league_id=league.id,
                player_id=player.id,
                status="T",
                on_team_id=team.id,
            )
        )


def set_value(
    db, player: Player, value: float, *, fmt: str = "redraft", source: str = "fantasycalc"
) -> None:
    db.add(
        TradeValue(
            player_id=player.id,
            source=source,
            format=fmt,
            value=value,
            fetched_at=_utcnow(),
        )
    )


# --- starter_slots (pure) --------------------------------------------------


def test_starter_slots_distributes_flex():
    slots = evaluator.starter_slots(
        {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1}
    )
    assert slots == {
        "QB": 1,
        "RB": 2.4,
        "WR": 3.5,
        "TE": 1.1,
        "K": 1,
        "DEF": 1,
    }


def test_starter_slots_ignores_bench_and_ir():
    slots = evaluator.starter_slots({"QB": 1, "BN": 6, "IR": 2})
    assert slots == {"QB": 1}


def test_starter_slots_without_flex_is_direct_counts():
    slots = evaluator.starter_slots({"QB": 1, "RB": 2, "WR": 2})
    assert slots == {"QB": 1, "RB": 2, "WR": 2}


def test_starter_slots_scales_multiple_flex_and_aliases():
    slots = evaluator.starter_slots({"RB": 2, "W/R/T": 2, "DST": 1})
    assert slots["RB"] == 2.8
    assert slots["WR"] == 1.0
    assert slots["TE"] == pytest.approx(0.2)
    assert slots["DEF"] == 1


# --- projections -----------------------------------------------------------


def test_best_projection_prefers_fantasypros(db_session):
    player = make_player(db_session, "Two Source", "RB", 100)
    db_session.add(
        Projection(
            player_id=player.id,
            source="fantasypros",
            season=SEASON,
            week=None,
            stat_json={"rush_yds": 2000},
            fetched_at=_utcnow(),
        )
    )
    db_session.commit()

    chosen = evaluator.best_projection(db_session, player.id, SEASON)
    assert chosen.source == "fantasypros"


def test_best_projection_ignores_weekly_rows(db_session):
    player = make_player(db_session, "Weekly Only", "RB")
    db_session.add(
        Projection(
            player_id=player.id,
            source="espn",
            season=SEASON,
            week=3,
            stat_json={"rush_yds": 500},
            fetched_at=_utcnow(),
        )
    )
    db_session.commit()

    assert evaluator.best_projection(db_session, player.id, SEASON) is None


def test_league_points_scores_stat_line_under_league_rules(db_session):
    player = make_player(db_session, "Receiver", "WR")
    projection = Projection(
        player_id=player.id,
        source="espn",
        season=SEASON,
        week=None,
        stat_json={"rec": 100, "rec_yds": 1000},
        fetched_at=_utcnow(),
    )

    half = evaluator.league_points(projection, scoring.get_preset("half_ppr"))
    full = evaluator.league_points(projection, scoring.get_preset("full_ppr"))
    assert half == 150.0
    assert full == 200.0
    assert evaluator.league_points(None, scoring.get_preset("half_ppr")) == 0.0


# --- replacement level -----------------------------------------------------


@pytest.fixture()
def pool_league(db_session) -> League:
    """2-team league with a deliberately uneven projected player pool."""
    league = make_league(db_session, roster_slots=FULL_SLOTS, num_teams=2)

    for index, points in enumerate([200, 180, 160, 140, 120, 100, 80]):
        make_player(db_session, f"RB{index + 1}", "RB", points)
    for index, points in enumerate([300, 250, 200]):
        make_player(db_session, f"QB{index + 1}", "QB", points)
    for index, points in enumerate([150, 140, 130]):
        make_player(db_session, f"WR{index + 1}", "WR", points)
    for index, points in enumerate([90, 70]):
        make_player(db_session, f"TE{index + 1}", "TE", points)

    db_session.commit()
    return league


def test_replacement_level_uses_league_wide_starter_demand(pool_league, db_session):
    # RB demand 2 + 0.4 flex = 2.4/team, 2 teams -> ceil(4.8) = 5 starters,
    # so the replacement RB is the 6th best: 100 points.
    levels = evaluator.replacement_levels(db_session, pool_league, SEASON)
    assert levels["RB"] == 100.0


def test_replacement_level_for_single_slot_position(pool_league, db_session):
    # QB: 1/team * 2 teams = 2 starters -> 3rd best QB.
    levels = evaluator.replacement_levels(db_session, pool_league, SEASON)
    assert levels["QB"] == 200.0


def test_replacement_level_positions_without_data_are_zero(pool_league, db_session):
    levels = evaluator.replacement_levels(db_session, pool_league, SEASON)
    assert levels["K"] == 0.0
    assert levels["DEF"] == 0.0


def test_replacement_level_shallow_pool_falls_back_to_worst(pool_league, db_session):
    # WR would need the 6th best but only 3 exist; TE would need the 4th of 2.
    levels = evaluator.replacement_levels(db_session, pool_league, SEASON)
    assert levels["WR"] == 130.0
    assert levels["TE"] == 70.0


def test_replacement_level_is_league_scored(db_session):
    """The same pool prices differently under PPR vs standard scoring."""
    half = make_league(
        db_session, roster_slots={"WR": 1}, num_teams=1, league_key="manual.half"
    )
    ppr = make_league(
        db_session,
        roster_slots={"WR": 1},
        num_teams=1,
        league_key="manual.ppr",
        preset="full_ppr",
    )
    for index in range(2):
        player = Player(full_name=f"WR{index}", position="WR")
        db_session.add(player)
        db_session.flush()
        db_session.add(
            Projection(
                player_id=player.id,
                source="espn",
                season=SEASON,
                week=None,
                stat_json={"rec": 100, "rec_yds": 1000},
                fetched_at=_utcnow(),
            )
        )
    db_session.commit()

    assert evaluator.replacement_levels(db_session, half, SEASON)["WR"] == 150.0
    assert evaluator.replacement_levels(db_session, ppr, SEASON)["WR"] == 200.0


# --- optimal lineup --------------------------------------------------------


@pytest.fixture()
def lineup_league(db_session) -> League:
    return make_league(db_session, roster_slots=SMALL_SLOTS, num_teams=2)


def test_optimal_lineup_fills_flex_with_best_leftover(lineup_league, db_session):
    qb = make_player(db_session, "QB1", "QB", 300)
    rb1 = make_player(db_session, "RB1", "RB", 150)
    rb2 = make_player(db_session, "RB2", "RB", 130)
    wr1 = make_player(db_session, "WR1", "WR", 120)
    db_session.commit()

    ids = [qb.id, rb1.id, rb2.id, wr1.id]
    # QB 300 + RB 150 + WR 120 + FLEX (2nd RB) 130
    assert evaluator.optimal_lineup_points(db_session, lineup_league, ids, SEASON) == 700.0


def test_optimal_lineup_ignores_surplus_at_a_capped_position(
    lineup_league, db_session
):
    qb1 = make_player(db_session, "QB1", "QB", 300)
    qb2 = make_player(db_session, "QB2", "QB", 290)
    rb1 = make_player(db_session, "RB1", "RB", 100)
    db_session.commit()

    # Only one QB slot and no QB-eligible flex: QB2 stays on the bench.
    points = evaluator.optimal_lineup_points(
        db_session, lineup_league, [qb1.id, qb2.id, rb1.id], SEASON
    )
    assert points == 400.0


def test_optimal_lineup_counts_unprojected_players_as_zero(lineup_league, db_session):
    qb = make_player(db_session, "QB1", "QB", 300)
    ghost = make_player(db_session, "No Projection RB", "RB", None)
    db_session.commit()

    points = evaluator.optimal_lineup_points(
        db_session, lineup_league, [qb.id, ghost.id], SEASON
    )
    assert points == 300.0


def test_optimal_lineup_of_empty_roster_is_zero(lineup_league, db_session):
    db_session.commit()
    assert evaluator.optimal_lineup_points(db_session, lineup_league, [], SEASON) == 0.0


# --- free agents -----------------------------------------------------------


@pytest.fixture()
def fa_league(db_session):
    """Small league where my team's starters and the FA pool are both known."""
    league = make_league(db_session, roster_slots=SMALL_SLOTS, num_teams=2)
    mine = make_team(db_session, league, "My Squad", is_my_team=True)
    rival = make_team(db_session, league, "Rivals")

    my_qb = make_player(db_session, "My QB", "QB", 300)
    my_rb = make_player(db_session, "My RB", "RB", 150)
    my_wr = make_player(db_session, "My WR", "WR", 120)
    my_te = make_player(db_session, "My TE", "TE", 60)
    roster(db_session, league, mine, my_qb, my_rb, my_wr, my_te)

    rival_rb = make_player(db_session, "Rival RB", "RB", 140)
    roster(db_session, league, rival, rival_rb)

    # Free agents.
    fa_rb = make_player(db_session, "Hot RB", "RB", 100)
    fa_wr = make_player(db_session, "Steady WR", "WR", 90)
    fa_qb = make_player(db_session, "Backup QB", "QB", 250)
    make_player(db_session, "Deep Cut K", "K", None)

    set_value(db_session, fa_rb, 2500)
    db_session.add(
        TrendingSignal(
            player_id=fa_rb.id,
            source="sleeper",
            kind="add",
            count=4212,
            fetched_at=_utcnow(),
        )
    )
    db_session.add(
        TrendingSignal(
            player_id=fa_wr.id,
            source="sleeper",
            kind="drop",
            count=999,
            fetched_at=_utcnow(),
        )
    )
    db_session.commit()
    return league, {"rb": fa_rb, "wr": fa_wr, "qb": fa_qb}


def test_free_agents_exclude_rostered_players(fa_league, db_session):
    league, _ = fa_league
    rows = evaluator.evaluate_free_agents(db_session, league, season=SEASON)["rows"]
    names = {row["full_name"] for row in rows}
    assert "My RB" not in names
    assert "Rival RB" not in names
    assert {"Hot RB", "Steady WR", "Backup QB", "Deep Cut K"} <= names


def test_free_agents_sorted_by_vor_with_projected_players_first(fa_league, db_session):
    league, _ = fa_league
    rows = evaluator.evaluate_free_agents(db_session, league, season=SEASON)["rows"]

    assert [row["full_name"] for row in rows[:3]] == [
        "Backup QB",
        "Hot RB",
        "Steady WR",
    ]
    assert rows[-1]["full_name"] == "Deep Cut K"
    assert rows[-1]["has_projection"] is False
    vors = [row["vor"] for row in rows if row["has_projection"]]
    assert vors == sorted(vors, reverse=True)


def test_free_agent_row_carries_points_value_and_trend(fa_league, db_session):
    league, _ = fa_league
    rows = {
        row["full_name"]: row
        for row in evaluator.evaluate_free_agents(db_session, league, season=SEASON)[
            "rows"
        ]
    }

    hot_rb = rows["Hot RB"]
    assert hot_rb["ros_points"] == 100.0
    assert hot_rb["ppg"] == round(100 / 17, 1)
    assert hot_rb["trade_value"] == 2500
    assert hot_rb["trending_add"] == 4212
    # A "drop" signal is not an add signal.
    assert rows["Steady WR"]["trending_add"] is None
    assert rows["Steady WR"]["trade_value"] is None


def test_free_agent_vor_subtracts_positional_replacement(fa_league, db_session):
    league, _ = fa_league
    levels = evaluator.replacement_levels(db_session, league, SEASON)
    rows = {
        row["full_name"]: row
        for row in evaluator.evaluate_free_agents(db_session, league, season=SEASON)[
            "rows"
        ]
    }
    assert rows["Hot RB"]["vor"] == round(100.0 - levels["RB"], 1)


def test_my_worst_starter_delta_uses_flex_pool_for_flex_positions(
    fa_league, db_session
):
    league, _ = fa_league
    rows = {
        row["full_name"]: row
        for row in evaluator.evaluate_free_agents(db_session, league, season=SEASON)[
            "rows"
        ]
    }

    # My starters: QB 300, RB 150, WR 120, FLEX = TE 60. The worst
    # FLEX-eligible starter is the 60-point TE, so every RB/WR/TE free agent
    # is compared against that, not against their own position's starter.
    assert rows["Hot RB"]["my_worst_starter_delta"] == 40.0
    assert rows["Steady WR"]["my_worst_starter_delta"] == 30.0
    # QB is not FLEX-eligible: compared with my only QB starter.
    assert rows["Backup QB"]["my_worst_starter_delta"] == -50.0
    # No kicker slot in this league, so no K starter to compare with.
    assert rows["Deep Cut K"]["my_worst_starter_delta"] is None


def test_my_worst_starter_delta_is_null_without_my_team(db_session):
    league = make_league(db_session, roster_slots=SMALL_SLOTS, num_teams=2)
    other = make_team(db_session, league, "Someone Else")
    roster(db_session, league, other, make_player(db_session, "Their RB", "RB", 150))
    make_player(db_session, "Free RB", "RB", 100)
    db_session.commit()

    rows = evaluator.evaluate_free_agents(db_session, league, season=SEASON)["rows"]
    assert all(row["my_worst_starter_delta"] is None for row in rows)


def test_free_agents_position_filter_and_limit(fa_league, db_session):
    league, _ = fa_league
    rbs = evaluator.evaluate_free_agents(
        db_session, league, position="rb", season=SEASON
    )["rows"]
    assert [row["full_name"] for row in rbs] == ["Hot RB"]

    limited = evaluator.evaluate_free_agents(
        db_session, league, limit=2, season=SEASON
    )["rows"]
    assert len(limited) == 2


def test_free_agents_for_yahoo_league_uses_status_rows(db_session):
    league = make_league(
        db_session,
        roster_slots=SMALL_SLOTS,
        num_teams=2,
        source="yahoo",
        league_key="461.l.1",
    )
    team = make_team(db_session, league, "My Squad", is_my_team=True)

    free = make_player(db_session, "Yahoo FA", "RB", 120)
    waived = make_player(db_session, "Yahoo Waiver", "WR", 110)
    taken = make_player(db_session, "Yahoo Rostered", "RB", 200)
    unlisted = make_player(db_session, "Not In League", "RB", 300)

    db_session.add_all(
        [
            LeaguePlayer(league_id=league.id, player_id=free.id, status="FA"),
            LeaguePlayer(league_id=league.id, player_id=waived.id, status="W"),
            LeaguePlayer(
                league_id=league.id,
                player_id=taken.id,
                status="T",
                on_team_id=team.id,
            ),
        ]
    )
    db_session.commit()

    names = {
        row["full_name"]
        for row in evaluator.evaluate_free_agents(db_session, league, season=SEASON)[
            "rows"
        ]
    }
    assert names == {"Yahoo FA", "Yahoo Waiver"}
    assert unlisted.full_name not in names


# --- current projection week -----------------------------------------------


def test_current_projection_week_is_none_without_weekly_rows(db_session):
    make_player(db_session, "Season Only", "RB", 200)
    db_session.commit()
    assert evaluator.current_projection_week(db_session, SEASON) is None


def test_current_projection_week_is_the_latest_week_on_file(db_session):
    player = make_player(db_session, "Weekly Guy", "RB", 200)
    set_week_points(db_session, player, 12, week=2)
    set_week_points(db_session, player, 14, week=3)
    db_session.commit()
    assert evaluator.current_projection_week(db_session, SEASON) == 3


def test_current_projection_week_ignores_other_seasons_and_sources(db_session):
    player = make_player(db_session, "Weekly Guy", "RB", 200)
    set_week_points(db_session, player, 12, week=3)
    # A source we never score from must not advance the week...
    set_week_points(db_session, player, 30, week=9, source="sleeper")
    # ...nor may another season's rows.
    db_session.add(
        Projection(
            player_id=player.id,
            source="espn",
            season=SEASON - 1,
            week=17,
            stat_json={"rush_yds": 100},
            fetched_at=_utcnow(),
        )
    )
    db_session.commit()
    assert evaluator.current_projection_week(db_session, SEASON) == 3


# --- position filter -------------------------------------------------------


def test_position_filter_expands_flex_and_folds_aliases():
    assert evaluator.position_filter(None) is None
    assert evaluator.position_filter("  ") is None
    assert evaluator.position_filter("rb") == ["RB"]
    assert evaluator.position_filter("FLEX") == ["RB", "WR", "TE"]
    assert evaluator.position_filter("w/r/t") == ["RB", "WR", "TE"]
    assert evaluator.position_filter("DST") == ["DEF"]


def test_free_agents_flex_filter_includes_only_rb_wr_te(fa_league, db_session):
    league, _ = fa_league
    rows = evaluator.evaluate_free_agents(
        db_session, league, position="FLEX", season=SEASON
    )["rows"]
    assert {row["full_name"] for row in rows} == {"Hot RB", "Steady WR"}
    assert {row["position"] for row in rows} <= {"RB", "WR", "TE"}

    # The Yahoo spelling of the same slot selects the same players.
    alias = evaluator.evaluate_free_agents(
        db_session, league, position="W/R/T", season=SEASON
    )["rows"]
    assert [row["full_name"] for row in alias] == [row["full_name"] for row in rows]


# --- weekly points ---------------------------------------------------------


@pytest.fixture()
def week_league(db_session):
    """fa_league's shape plus week-1 projections, so weekly maths is checkable.

    My ROS-optimal starters are QB 300 / RB 150 / WR 120 / FLEX = the TE (60),
    with the 50-point RB on the bench. Their week-1 points are 20/12/10/4.
    """
    league = make_league(db_session, roster_slots=SMALL_SLOTS, num_teams=2)
    mine = make_team(db_session, league, "My Squad", is_my_team=True)

    my_qb = make_player(db_session, "My QB", "QB", 300)
    my_rb = make_player(db_session, "My RB", "RB", 150)
    my_wr = make_player(db_session, "My WR", "WR", 120)
    my_te = make_player(db_session, "My TE", "TE", 60)
    my_bench = make_player(db_session, "My Bench RB", "RB", 50)
    roster(db_session, league, mine, my_qb, my_rb, my_wr, my_te, my_bench)

    for player, points in (
        (my_qb, 20),
        (my_rb, 12),
        (my_wr, 10),
        (my_te, 4),
        (my_bench, 3),
    ):
        set_week_points(db_session, player, points)

    fa_rb = make_player(db_session, "Hot RB", "RB", 100)
    fa_wr = make_player(db_session, "Steady WR", "WR", 90)  # no weekly row
    fa_qb = make_player(db_session, "Backup QB", "QB", 250)
    set_week_points(db_session, fa_rb, 9)
    set_week_points(db_session, fa_qb, 15)

    db_session.commit()
    return league, {"rb": fa_rb, "wr": fa_wr, "qb": fa_qb, "te": my_te}


def _by_name(result: dict) -> dict:
    return {row["full_name"]: row for row in result["rows"]}


def test_free_agents_report_the_projection_week(week_league, db_session):
    league, _ = week_league
    result = evaluator.evaluate_free_agents(db_session, league, season=SEASON)
    assert result["week"] == 1


def test_week_points_come_from_the_current_weeks_projection(week_league, db_session):
    league, _ = week_league
    rows = _by_name(evaluator.evaluate_free_agents(db_session, league, season=SEASON))
    assert rows["Hot RB"]["week_points"] == 9.0
    assert rows["Backup QB"]["week_points"] == 15.0


def test_week_points_are_null_without_a_weekly_projection(week_league, db_session):
    league, _ = week_league
    rows = _by_name(evaluator.evaluate_free_agents(db_session, league, season=SEASON))
    assert rows["Steady WR"]["week_points"] is None
    assert rows["Steady WR"]["week_delta"] is None
    # ...but the season-long numbers are unaffected.
    assert rows["Steady WR"]["ros_points"] == 90.0


def test_week_points_prefer_fantasypros_over_espn(week_league, db_session):
    league, f = week_league
    set_week_points(db_session, f["rb"], 11, source="fantasypros")
    db_session.commit()

    rows = _by_name(evaluator.evaluate_free_agents(db_session, league, season=SEASON))
    assert rows["Hot RB"]["week_points"] == 11.0


def test_week_points_ignore_other_weeks(week_league, db_session):
    league, f = week_league
    # Week 1 is the latest week on file, so a stale week-0 row must not win.
    set_week_points(db_session, f["rb"], 99, week=0)
    db_session.commit()

    rows = _by_name(evaluator.evaluate_free_agents(db_session, league, season=SEASON))
    assert rows["Hot RB"]["week_points"] == 9.0


def test_week_delta_uses_the_flex_aware_worst_starter(week_league, db_session):
    league, _ = week_league
    rows = _by_name(evaluator.evaluate_free_agents(db_session, league, season=SEASON))

    # Worst FLEX-eligible starter this week is the TE's 4 points.
    assert rows["Hot RB"]["week_delta"] == 5.0
    # QB is not FLEX-eligible: compared with my QB starter's 20.
    assert rows["Backup QB"]["week_delta"] == -5.0
    # The bench RB (3 points) is not a starter and never sets the baseline.


def test_week_delta_baseline_matches_the_ros_starter_set(week_league, db_session):
    """The 3-point bench RB would be a cheaper baseline -- but it isn't a starter."""
    league, _ = week_league
    rows = _by_name(evaluator.evaluate_free_agents(db_session, league, season=SEASON))
    assert rows["Hot RB"]["week_delta"] != 9.0 - 3.0


def test_week_delta_is_null_when_the_baseline_starter_has_no_week_points(
    week_league, db_session
):
    league, _f = week_league
    # Drop every weekly projection from my own roster: no baseline is computable.
    for name in ("My QB", "My RB", "My WR", "My TE", "My Bench RB"):
        player = db_session.query(Player).filter(Player.full_name == name).one()
        db_session.query(Projection).filter(
            Projection.player_id == player.id, Projection.week.isnot(None)
        ).delete()
    db_session.commit()

    rows = _by_name(evaluator.evaluate_free_agents(db_session, league, season=SEASON))
    assert rows["Hot RB"]["week_points"] == 9.0
    assert rows["Hot RB"]["week_delta"] is None
    # The ROS comparison still works -- it never needed weekly numbers.
    assert rows["Hot RB"]["my_worst_starter_delta"] == 40.0


def test_week_columns_are_null_without_any_weekly_projections(fa_league, db_session):
    league, _ = fa_league
    result = evaluator.evaluate_free_agents(db_session, league, season=SEASON)
    assert result["week"] is None
    assert all(row["week_points"] is None for row in result["rows"])
    assert all(row["week_delta"] is None for row in result["rows"])


# --- my players ------------------------------------------------------------


@pytest.fixture()
def full_lineup_league(db_session):
    """A full-slot league whose optimal lineup is deliberately not points-ordered."""
    league = make_league(db_session, roster_slots=FULL_SLOTS, num_teams=2)
    mine = make_team(db_session, league, "My Squad", is_my_team=True)
    rival = make_team(db_session, league, "Rivals")

    players = {
        "QB1": make_player(db_session, "QB1", "QB", 300),
        "RB1": make_player(db_session, "RB1", "RB", 200),
        "RB2": make_player(db_session, "RB2", "RB", 180),
        "RB3": make_player(db_session, "RB3", "RB", 90),
        "WR1": make_player(db_session, "WR1", "WR", 170),
        "WR2": make_player(db_session, "WR2", "WR", 160),
        "WR3": make_player(db_session, "WR3", "WR", 100),
        "TE1": make_player(db_session, "TE1", "TE", 80),
        "K1": make_player(db_session, "K1", "K", 110),
        "DEF1": make_player(db_session, "DEF1", "DEF", 120),
    }
    roster(db_session, league, mine, *players.values())
    set_week_points(db_session, players["RB1"], 18)

    rival_qb = make_player(db_session, "Rival QB", "QB", 280)
    rival_rb = make_player(db_session, "Rival RB", "RB", 140)
    roster(db_session, league, rival, rival_qb, rival_rb)

    db_session.commit()
    return league, mine, rival, players


def test_my_players_are_starters_first_in_slot_order_then_bench(
    full_lineup_league, db_session
):
    league, _mine, _rival, _players = full_lineup_league
    mine = evaluator.evaluate_free_agents(db_session, league, season=SEASON)[
        "my_players"
    ]

    assert [(row["starter_slot"], row["full_name"]) for row in mine] == [
        ("QB", "QB1"),
        ("RB", "RB1"),
        ("RB", "RB2"),
        ("WR", "WR1"),
        ("WR", "WR2"),
        ("TE", "TE1"),
        ("FLEX", "WR3"),
        ("K", "K1"),
        ("DEF", "DEF1"),
        (None, "RB3"),
    ]
    assert [row["is_starter"] for row in mine] == [True] * 9 + [False]


def test_my_players_flex_occupant_matches_the_optimal_lineup(
    full_lineup_league, db_session
):
    league, mine, _rival, _players = full_lineup_league
    my_players = evaluator.evaluate_free_agents(db_session, league, season=SEASON)[
        "my_players"
    ]
    lineup = evaluator.team_lineup(db_session, league, mine.id, season=SEASON)

    flex = next(row for row in my_players if row["starter_slot"] == "FLEX")
    # WR3 (100) beats RB3 (90) for the flex spot in both views.
    assert flex["full_name"] == "WR3"
    assert [
        (entry["slot"], entry["player"]["full_name"])
        for entry in lineup["slots"]
        if entry["player"] is not None
    ] == [
        (row["starter_slot"], row["full_name"]) for row in my_players if row["is_starter"]
    ]


def test_my_players_carry_points_and_week_points(full_lineup_league, db_session):
    league, _mine, _rival, _players = full_lineup_league
    mine = {
        row["full_name"]: row
        for row in evaluator.evaluate_free_agents(db_session, league, season=SEASON)[
            "my_players"
        ]
    }
    assert mine["RB1"]["ros_points"] == 200.0
    assert mine["RB1"]["ppg"] == round(200 / 17, 1)
    assert mine["RB1"]["week_points"] == 18.0
    assert mine["RB2"]["week_points"] is None
    assert mine["RB3"]["is_starter"] is False
    assert mine["RB3"]["starter_slot"] is None


def test_my_players_respect_the_position_filter(full_lineup_league, db_session):
    league, _mine, _rival, _players = full_lineup_league
    flex = evaluator.evaluate_free_agents(
        db_session, league, position="FLEX", season=SEASON
    )["my_players"]
    assert {row["full_name"] for row in flex} == {
        "RB1",
        "RB2",
        "RB3",
        "WR1",
        "WR2",
        "WR3",
        "TE1",
    }
    # Filtering the display never changes who is starting.
    assert next(row for row in flex if row["full_name"] == "WR3")["starter_slot"] == "FLEX"

    qbs = evaluator.evaluate_free_agents(
        db_session, league, position="QB", season=SEASON
    )["my_players"]
    assert [row["full_name"] for row in qbs] == ["QB1"]


def test_my_players_is_empty_without_a_my_team(db_session):
    league = make_league(db_session, roster_slots=SMALL_SLOTS, num_teams=2)
    other = make_team(db_session, league, "Someone Else")
    roster(db_session, league, other, make_player(db_session, "Their RB", "RB", 150))
    make_player(db_session, "Free RB", "RB", 100)
    db_session.commit()

    assert evaluator.evaluate_free_agents(db_session, league, season=SEASON)[
        "my_players"
    ] == []


# --- team_lineup -----------------------------------------------------------


def seats(lineup: dict) -> list[tuple[str, str | None]]:
    """``[(slot, occupant name or None)]`` for every seat in the lineup."""
    return [
        (entry["slot"], entry["player"]["full_name"] if entry["player"] else None)
        for entry in lineup["slots"]
    ]


def test_team_lineup_fills_every_slot_and_benches_the_rest(
    full_lineup_league, db_session
):
    league, mine, _rival, _players = full_lineup_league
    lineup = evaluator.team_lineup(db_session, league, mine.id, season=SEASON)

    assert lineup["week"] == 1
    assert lineup["source"] == "auto"
    assert seats(lineup) == [
        ("QB", "QB1"),
        ("RB", "RB1"),
        ("RB", "RB2"),
        ("WR", "WR1"),
        ("WR", "WR2"),
        ("TE", "TE1"),
        ("FLEX", "WR3"),
        ("K", "K1"),
        ("DEF", "DEF1"),
    ]
    assert [row["full_name"] for row in lineup["bench"]] == ["RB3"]

    starter = lineup["slots"][1]
    assert starter == {
        "slot": "RB",
        "player": {
            "player_id": _players["RB1"].id,
            "full_name": "RB1",
            "position": "RB",
            "nfl_team": "SF",
            "week_points": 18.0,
            "ros_points": 200.0,
        },
    }
    assert "slot" not in lineup["bench"][0]
    assert lineup["bench"][0]["ros_points"] == 90.0
    assert lineup["bench"][0]["week_points"] is None


def test_team_lineup_works_for_any_team(full_lineup_league, db_session):
    league, _mine, rival, _players = full_lineup_league
    lineup = evaluator.team_lineup(db_session, league, rival.id, season=SEASON)

    # A two-player roster fills only the slots it can; the rest render empty.
    assert seats(lineup) == [
        ("QB", "Rival QB"),
        ("RB", "Rival RB"),
        ("RB", None),
        ("WR", None),
        ("WR", None),
        ("TE", None),
        ("FLEX", None),
        ("K", None),
        ("DEF", None),
    ]
    assert lineup["bench"] == []


def test_team_lineup_of_an_empty_roster_is_all_empty_slots(
    full_lineup_league, db_session
):
    league, _mine, _rival, _players = full_lineup_league
    empty = make_team(db_session, league, "Nobody")
    db_session.commit()

    lineup = evaluator.team_lineup(db_session, league, empty.id, season=SEASON)
    assert lineup["source"] == "auto"
    assert all(entry["player"] is None for entry in lineup["slots"])
    assert [entry["slot"] for entry in lineup["slots"]] == [
        "QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"
    ]
    assert lineup["bench"] == []


# --- saved (manual) lineups ------------------------------------------------


def save_lineup(db, team: Team, assignments: dict[Player, str]) -> None:
    """Store an owner-chosen lineup the way the manual router does."""
    db.query(RosterSlot).filter(
        RosterSlot.team_id == team.id,
        RosterSlot.week == evaluator.MANUAL_LINEUP_WEEK,
    ).delete(synchronize_session=False)
    for player, slot in assignments.items():
        db.add(
            RosterSlot(
                team_id=team.id,
                week=evaluator.MANUAL_LINEUP_WEEK,
                player_id=player.id,
                selected_position=slot,
            )
        )
    db.commit()


def test_manual_lineup_is_none_without_saved_rows(full_lineup_league, db_session):
    _league, mine, _rival, _players = full_lineup_league
    assert evaluator.manual_lineup(db_session, mine.id) is None


def test_manual_lineup_reads_back_normalized_slots(full_lineup_league, db_session):
    _league, mine, _rival, players = full_lineup_league
    save_lineup(db_session, mine, {players["QB1"]: "QB", players["RB3"]: "W/R/T"})

    assert evaluator.manual_lineup(db_session, mine.id) == {
        players["QB1"].id: "QB",
        players["RB3"].id: "FLEX",
    }


def test_team_lineup_prefers_the_saved_lineup(full_lineup_league, db_session):
    league, mine, _rival, players = full_lineup_league
    # Deliberately not the optimal lineup: RB3 (90) starts over RB2 (180),
    # and the FLEX holds RB2 rather than WR3.
    save_lineup(
        db_session,
        mine,
        {
            players["QB1"]: "QB",
            players["RB1"]: "RB",
            players["RB3"]: "RB",
            players["RB2"]: "FLEX",
        },
    )
    lineup = evaluator.team_lineup(db_session, league, mine.id, season=SEASON)

    assert lineup["source"] == "manual"
    assert seats(lineup) == [
        ("QB", "QB1"),
        ("RB", "RB1"),
        ("RB", "RB3"),
        ("WR", None),
        ("WR", None),
        ("TE", None),
        ("FLEX", "RB2"),
        ("K", None),
        ("DEF", None),
    ]
    # Everyone the owner did not start is bench, best ROS points first.
    assert [row["full_name"] for row in lineup["bench"]] == [
        "WR1",
        "WR2",
        "DEF1",
        "K1",
        "WR3",
        "TE1",
    ]


def test_team_lineup_reverts_to_auto_when_the_saved_lineup_is_cleared(
    full_lineup_league, db_session
):
    league, mine, _rival, players = full_lineup_league
    save_lineup(db_session, mine, {players["RB3"]: "RB"})
    assert evaluator.team_lineup(db_session, league, mine.id, season=SEASON)[
        "source"
    ] == "manual"

    db_session.query(RosterSlot).filter(RosterSlot.team_id == mine.id).delete()
    db_session.commit()

    lineup = evaluator.team_lineup(db_session, league, mine.id, season=SEASON)
    assert lineup["source"] == "auto"
    assert seats(lineup)[1] == ("RB", "RB1")


def test_team_lineup_ignores_stale_saved_rows(full_lineup_league, db_session):
    """Dropped players, unknown slots and overflow never reach the response."""
    league, mine, rival, players = full_lineup_league
    rival_rb = db_session.query(Player).filter(Player.full_name == "Rival RB").one()
    save_lineup(
        db_session,
        mine,
        {
            players["RB1"]: "RB",
            players["RB2"]: "RB",
            players["RB3"]: "RB",  # a third RB: the league has only two seats
            rival_rb: "WR",  # not on this roster at all
            players["TE1"]: "BN",  # not a starting slot
        },
    )
    lineup = evaluator.team_lineup(db_session, league, mine.id, season=SEASON)

    assert seats(lineup) == [
        ("QB", None),
        ("RB", "RB1"),
        ("RB", "RB2"),
        ("WR", None),
        ("WR", None),
        ("TE", None),
        ("FLEX", None),
        ("K", None),
        ("DEF", None),
    ]
    assert "RB3" in {row["full_name"] for row in lineup["bench"]}
    assert "Rival RB" not in {row["full_name"] for row in lineup["bench"]}


def test_my_players_follow_the_saved_lineup(full_lineup_league, db_session):
    league, mine, _rival, players = full_lineup_league
    save_lineup(
        db_session, mine, {players["RB3"]: "RB", players["WR3"]: "FLEX"}
    )

    rows = {
        row["full_name"]: row
        for row in evaluator.evaluate_free_agents(db_session, league, season=SEASON)[
            "my_players"
        ]
    }
    assert rows["RB3"]["is_starter"] is True
    assert rows["RB3"]["starter_slot"] == "RB"
    assert rows["WR3"]["starter_slot"] == "FLEX"
    # The ROS-optimal starters are on the bench now that the owner said so.
    assert rows["RB1"]["is_starter"] is False
    assert rows["QB1"]["is_starter"] is False


def test_deltas_are_measured_against_the_saved_starters(
    full_lineup_league, db_session
):
    league, mine, _rival, players = full_lineup_league
    set_week_points(db_session, players["RB3"], 5)
    fa = make_player(db_session, "Hot FA RB", "RB", 150)
    set_week_points(db_session, fa, 12)
    db_session.commit()

    auto = _by_name(evaluator.evaluate_free_agents(db_session, league, season=SEASON))
    # Auto starters: the worst FLEX-eligible ROS starter is TE1 (80).
    assert auto["Hot FA RB"]["my_worst_starter_delta"] == 70.0

    save_lineup(db_session, mine, {players["RB3"]: "RB", players["QB1"]: "QB"})
    manual = _by_name(evaluator.evaluate_free_agents(db_session, league, season=SEASON))

    # Now the only FLEX-eligible starter is RB3: 90 ROS points, 5 this week.
    assert manual["Hot FA RB"]["my_worst_starter_delta"] == 60.0
    assert manual["Hot FA RB"]["week_delta"] == 7.0


def test_a_position_with_no_saved_starter_has_no_baseline(
    full_lineup_league, db_session
):
    """Bench-only edge case: no starter at a position means a null delta."""
    league, mine, _rival, players = full_lineup_league
    save_lineup(db_session, mine, {players["QB1"]: "QB"})
    make_player(db_session, "Hot FA RB", "RB", 150)
    db_session.commit()

    rows = _by_name(evaluator.evaluate_free_agents(db_session, league, season=SEASON))
    assert rows["Hot FA RB"]["my_worst_starter_delta"] is None
    assert rows["Hot FA RB"]["week_delta"] is None


# --- trades ----------------------------------------------------------------


@pytest.fixture()
def trade_league(db_session):
    league = make_league(db_session, roster_slots=SMALL_SLOTS, num_teams=2)
    team_a = make_team(db_session, league, "Alpha", is_my_team=True)
    team_b = make_team(db_session, league, "Bravo")

    a_star = make_player(db_session, "A Star RB", "RB", 200)
    a_depth = make_player(db_session, "A Depth WR", "WR", 60)
    a_qb = make_player(db_session, "A QB", "QB", 300)
    roster(db_session, league, team_a, a_star, a_depth, a_qb)

    b_star = make_player(db_session, "B Star WR", "WR", 190)
    b_depth = make_player(db_session, "B Depth RB", "RB", 50)
    b_qb = make_player(db_session, "B QB", "QB", 280)
    roster(db_session, league, team_b, b_star, b_depth, b_qb)

    set_value(db_session, a_star, 5000)
    set_value(db_session, a_depth, 500)
    set_value(db_session, b_star, 5000)
    set_value(db_session, b_depth, 400)
    set_value(db_session, a_star, 4000, fmt="dynasty")
    set_value(db_session, b_star, 9000, fmt="dynasty")

    db_session.commit()
    return league, {
        "team_a": team_a,
        "team_b": team_b,
        "a_star": a_star,
        "a_depth": a_depth,
        "b_star": b_star,
        "b_depth": b_depth,
    }


def test_trade_even_values_are_fair(trade_league, db_session):
    league, f = trade_league
    result = evaluator.evaluate_trade(
        db_session,
        league,
        {"team_id": f["team_a"].id, "player_ids": [f["a_star"].id]},
        {"team_id": f["team_b"].id, "player_ids": [f["b_star"].id]},
        season=SEASON,
    )
    assert result["verdict"] == "fair"
    assert result["margin_pct"] == 0.0


def test_trade_verdict_favors_the_side_receiving_more_value(trade_league, db_session):
    league, f = trade_league
    # A sends its 500-value depth WR and receives B's 5000-value star.
    result = evaluator.evaluate_trade(
        db_session,
        league,
        {"team_id": f["team_a"].id, "player_ids": [f["a_depth"].id]},
        {"team_id": f["team_b"].id, "player_ids": [f["b_star"].id]},
        season=SEASON,
    )
    assert result["verdict"] == "favors_a"
    assert result["margin_pct"] == pytest.approx(0.9)


def test_trade_verdict_favors_b_when_reversed(trade_league, db_session):
    league, f = trade_league
    result = evaluator.evaluate_trade(
        db_session,
        league,
        {"team_id": f["team_a"].id, "player_ids": [f["a_star"].id]},
        {"team_id": f["team_b"].id, "player_ids": [f["b_depth"].id]},
        season=SEASON,
    )
    assert result["verdict"] == "favors_b"
    assert result["margin_pct"] == pytest.approx(0.92)


def test_trade_margin_within_ten_percent_is_still_fair(trade_league, db_session):
    league, f = trade_league
    # 4600 vs 5000 -> 8% margin.
    db_session.query(TradeValue).filter(
        TradeValue.player_id == f["a_star"].id, TradeValue.format == "redraft"
    ).update({"value": 4600})
    db_session.commit()

    result = evaluator.evaluate_trade(
        db_session,
        league,
        {"team_id": f["team_a"].id, "player_ids": [f["a_star"].id]},
        {"team_id": f["team_b"].id, "player_ids": [f["b_star"].id]},
        season=SEASON,
    )
    assert result["verdict"] == "fair"
    assert result["margin_pct"] == pytest.approx(0.08)


def test_trade_reports_per_side_players_and_totals(trade_league, db_session):
    league, f = trade_league
    result = evaluator.evaluate_trade(
        db_session,
        league,
        {"team_id": f["team_a"].id, "player_ids": [f["a_star"].id, f["a_depth"].id]},
        {"team_id": f["team_b"].id, "player_ids": [f["b_star"].id]},
        season=SEASON,
    )

    side_a = result["sides"]["a"]
    assert side_a["team_name"] == "Alpha"
    assert {p["full_name"] for p in side_a["players"]} == {"A Star RB", "A Depth WR"}
    assert side_a["ros_points_total"] == 260.0
    assert side_a["value_total"] == 5500
    assert result["sides"]["b"]["players"][0]["ppg"] == round(190 / 17, 1)


def test_trade_lineup_deltas_reflect_the_swap(trade_league, db_session):
    league, f = trade_league
    result = evaluator.evaluate_trade(
        db_session,
        league,
        {"team_id": f["team_a"].id, "player_ids": [f["a_depth"].id]},
        {"team_id": f["team_b"].id, "player_ids": [f["b_star"].id]},
        season=SEASON,
    )

    side_a = result["sides"]["a"]
    # Before: QB 300 + RB 200 + WR 60 = 560 (no 4th starter available).
    # After:  QB 300 + RB 200 + WR 190 = 690.
    assert side_a["lineup_points_before"] == 560.0
    assert side_a["lineup_points_after"] == 690.0
    assert side_a["lineup_delta"] == 130.0

    side_b = result["sides"]["b"]
    # Before: QB 280 + RB 50 + WR 190 = 520.
    # After:  QB 280 + RB 50 + WR 60 = 390.
    assert side_b["lineup_delta"] == -130.0
    assert any("Alpha's optimal lineup changes by +130.0" in n for n in result["notes"])
    assert any("Bravo's optimal lineup changes by -130.0" in n for n in result["notes"])


def test_trade_warns_about_missing_projections_and_values(trade_league, db_session):
    league, f = trade_league
    ghost = make_player(db_session, "Ghost TE", "TE", None)
    roster(db_session, league, f["team_b"], ghost)
    db_session.commit()

    result = evaluator.evaluate_trade(
        db_session,
        league,
        {"team_id": f["team_a"].id, "player_ids": [f["a_star"].id]},
        {"team_id": f["team_b"].id, "player_ids": [ghost.id]},
        season=SEASON,
    )
    assert any("No rest-of-season projection for Ghost TE" in n for n in result["notes"])
    assert any("No market value for Ghost TE" in n for n in result["notes"])
    assert result["sides"]["b"]["players"][0]["ros_points"] == 0.0


def test_keeper_league_notes_dynasty_disagreement(trade_league, db_session):
    league, f = trade_league
    league.is_keeper = True
    db_session.commit()

    # Redraft values are identical (fair) but dynasty is 4000 vs 9000.
    result = evaluator.evaluate_trade(
        db_session,
        league,
        {"team_id": f["team_a"].id, "player_ids": [f["a_star"].id]},
        {"team_id": f["team_b"].id, "player_ids": [f["b_star"].id]},
        season=SEASON,
    )
    assert result["verdict"] == "fair"
    assert any("dynasty" in note for note in result["notes"])
    assert any("favors a" in note for note in result["notes"])


def test_redraft_league_skips_the_dynasty_note(trade_league, db_session):
    league, f = trade_league
    result = evaluator.evaluate_trade(
        db_session,
        league,
        {"team_id": f["team_a"].id, "player_ids": [f["a_star"].id]},
        {"team_id": f["team_b"].id, "player_ids": [f["b_star"].id]},
        season=SEASON,
    )
    assert not any("dynasty" in note for note in result["notes"])


# --- trade validation ------------------------------------------------------


def test_trade_rejects_player_not_on_that_team(trade_league, db_session):
    league, f = trade_league
    with pytest.raises(evaluator.TradeValidationError) as exc:
        evaluator.evaluate_trade(
            db_session,
            league,
            {"team_id": f["team_a"].id, "player_ids": [f["b_star"].id]},
            {"team_id": f["team_b"].id, "player_ids": [f["b_depth"].id]},
            season=SEASON,
        )
    assert "not on Alpha" in str(exc.value)


def test_trade_rejects_overlapping_players(trade_league, db_session):
    league, f = trade_league
    with pytest.raises(evaluator.TradeValidationError) as exc:
        evaluator.evaluate_trade(
            db_session,
            league,
            {"team_id": f["team_a"].id, "player_ids": [f["a_star"].id]},
            {"team_id": f["team_b"].id, "player_ids": [f["a_star"].id]},
            season=SEASON,
        )
    assert "both sides" in str(exc.value)


def test_trade_rejects_empty_side(trade_league, db_session):
    league, f = trade_league
    with pytest.raises(evaluator.TradeValidationError) as exc:
        evaluator.evaluate_trade(
            db_session,
            league,
            {"team_id": f["team_a"].id, "player_ids": [f["a_star"].id]},
            {"team_id": f["team_b"].id, "player_ids": []},
            season=SEASON,
        )
    assert "at least one player" in str(exc.value)


def test_trade_rejects_team_from_another_league(trade_league, db_session):
    league, f = trade_league
    other = make_league(
        db_session, roster_slots=SMALL_SLOTS, num_teams=2, league_key="manual.2"
    )
    outsider = make_team(db_session, other, "Outsider")
    db_session.commit()

    with pytest.raises(evaluator.TradeValidationError) as exc:
        evaluator.evaluate_trade(
            db_session,
            league,
            {"team_id": f["team_a"].id, "player_ids": [f["a_star"].id]},
            {"team_id": outsider.id, "player_ids": [f["b_star"].id]},
            season=SEASON,
        )
    assert "not in league" in str(exc.value)


def test_trade_rejects_same_team_on_both_sides(trade_league, db_session):
    league, f = trade_league
    with pytest.raises(evaluator.TradeValidationError) as exc:
        evaluator.evaluate_trade(
            db_session,
            league,
            {"team_id": f["team_a"].id, "player_ids": [f["a_star"].id]},
            {"team_id": f["team_a"].id, "player_ids": [f["a_depth"].id]},
            season=SEASON,
        )
    assert "two different teams" in str(exc.value)


def test_trade_rejects_duplicate_player_within_a_side(trade_league, db_session):
    league, f = trade_league
    with pytest.raises(evaluator.TradeValidationError) as exc:
        evaluator.evaluate_trade(
            db_session,
            league,
            {
                "team_id": f["team_a"].id,
                "player_ids": [f["a_star"].id, f["a_star"].id],
            },
            {"team_id": f["team_b"].id, "player_ids": [f["b_star"].id]},
            season=SEASON,
        )
    assert "same player twice" in str(exc.value)
