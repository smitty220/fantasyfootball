"""Selectable projection sources: parsing, averaging, and the endpoints.

The maths under test is always the same: with no selection the best source in
priority order wins outright, and with a selection every chosen source that has
a row is scored under the league's rules and those *points* are averaged.
"""

from __future__ import annotations

import pytest

from app.models import Player, Projection
from app.services import evaluator
from tests.test_evaluator import (
    SEASON,
    SMALL_SLOTS,
    _utcnow,
    make_league,
    make_player,
    make_team,
    roster,
    set_value,
    set_week_points,
    week_league,  # noqa: F401  (fixture reused for the backward-compat check)
)

BOTH = ("fantasypros", "espn")


# --- seeding helpers -------------------------------------------------------


def set_season_points(db, player: Player, points: float, *, source: str) -> None:
    """A season-long projection worth exactly ``points`` in half-PPR."""
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


def _rows(result: dict) -> dict[str, dict]:
    return {row["full_name"]: row for row in result["rows"]}


def _my_rows(result: dict) -> dict[str, dict]:
    return {row["full_name"]: row for row in result["my_players"]}


# --- validate_sources ------------------------------------------------------


def test_validate_sources_returns_none_for_no_selection():
    assert evaluator.validate_sources(None) is None
    assert evaluator.validate_sources("") is None
    assert evaluator.validate_sources("   ") is None
    assert evaluator.validate_sources(" , ") is None


def test_validate_sources_parses_trims_and_lowercases():
    assert evaluator.validate_sources("espn") == ("espn",)
    assert evaluator.validate_sources(" FantasyPros , ESPN ") == ("fantasypros", "espn")


def test_validate_sources_normalizes_order_and_duplicates():
    # Whatever order they arrive in, the tuple reads back in priority order.
    assert evaluator.validate_sources("espn,fantasypros") == ("fantasypros", "espn")
    assert evaluator.validate_sources("espn,espn") == ("espn",)


def test_validate_sources_rejects_an_unknown_source():
    with pytest.raises(ValueError) as exc:
        evaluator.validate_sources("fantasypros,sleeper")
    assert "sleeper" in str(exc.value)
    # ...and it says what the caller could have asked for instead.
    assert "fantasypros" in str(exc.value)


def test_available_sources_matches_the_priority_order():
    assert evaluator.AVAILABLE_SOURCES == ("fantasypros", "espn")


# --- the blended league ----------------------------------------------------


@pytest.fixture()
def blend_league(db_session):
    """A league whose players are deliberately projected differently per source.

    ============  ====  ====  ========  =============================
    player        espn  fp    priority  average of both
    ============  ====  ====  ========  =============================
    My QB          200   400       400  300
    My RB          100     -       100  100  (espn alone; never halved)
    My WR           60    80        80   70
    My TE           40    20        20   30
    Both FA RB     100   200       200  150
    Espn Only WR    90     -        90   90
    Fp Only TE       -    50        50   50
    ============  ====  ====  ========  =============================

    Week-1 rows follow the same pattern at a tenth of the size.
    """
    league = make_league(db_session, roster_slots=SMALL_SLOTS, num_teams=2)
    mine = make_team(db_session, league, "My Squad", is_my_team=True)

    my_qb = make_player(db_session, "My QB", "QB", 200)
    my_rb = make_player(db_session, "My RB", "RB", 100)
    my_wr = make_player(db_session, "My WR", "WR", 60)
    my_te = make_player(db_session, "My TE", "TE", 40)
    roster(db_session, league, mine, my_qb, my_rb, my_wr, my_te)

    set_season_points(db_session, my_qb, 400, source="fantasypros")
    set_season_points(db_session, my_wr, 80, source="fantasypros")
    set_season_points(db_session, my_te, 20, source="fantasypros")

    for player, espn_week, fp_week in (
        (my_qb, 20, 30),
        (my_rb, 10, None),
        (my_wr, 6, 4),
        (my_te, 2, 8),
    ):
        set_week_points(db_session, player, espn_week)
        if fp_week is not None:
            set_week_points(db_session, player, fp_week, source="fantasypros")

    both = make_player(db_session, "Both FA RB", "RB", 100)
    set_season_points(db_session, both, 200, source="fantasypros")
    set_week_points(db_session, both, 12)
    set_week_points(db_session, both, 18, source="fantasypros")

    espn_only = make_player(db_session, "Espn Only WR", "WR", 90)
    set_week_points(db_session, espn_only, 9)

    fp_only = make_player(db_session, "Fp Only TE", "TE", 50, source="fantasypros")
    set_week_points(db_session, fp_only, 5, source="fantasypros")

    # Neither selectable source covers these two.
    make_player(db_session, "Ghost K", "K", None)
    make_player(db_session, "Sleeper Only RB", "RB", 300, source="sleeper")

    db_session.commit()
    return league, mine


# --- season-long averaging -------------------------------------------------


def test_season_points_average_the_selected_sources(blend_league, db_session):
    league, _mine = blend_league
    rows = _rows(evaluator.evaluate_free_agents(db_session, league, sources=BOTH, season=SEASON))
    assert rows["Both FA RB"]["ros_points"] == 150.0
    assert rows["Both FA RB"]["ppg"] == round(150 / 17, 1)


def test_season_points_of_a_one_source_player_are_not_halved(blend_league, db_session):
    league, _mine = blend_league
    rows = _rows(evaluator.evaluate_free_agents(db_session, league, sources=BOTH, season=SEASON))
    # A source with no row for this player is excluded from *his* average
    # rather than zero-filled: 90, not 45.
    assert rows["Espn Only WR"]["ros_points"] == 90.0
    assert rows["Fp Only TE"]["ros_points"] == 50.0
    assert rows["Espn Only WR"]["has_projection"] is True


def test_season_points_are_zero_when_no_selected_source_has_a_row(
    blend_league, db_session
):
    league, _mine = blend_league
    rows = _rows(evaluator.evaluate_free_agents(db_session, league, sources=BOTH, season=SEASON))
    for name in ("Ghost K", "Sleeper Only RB"):
        assert rows[name]["has_projection"] is False
        assert rows[name]["ros_points"] == 0.0
        assert rows[name]["week_points"] is None


def test_single_source_selection_isolates_that_source(blend_league, db_session):
    league, _mine = blend_league
    espn = _rows(
        evaluator.evaluate_free_agents(db_session, league, sources=("espn",), season=SEASON)
    )
    fantasypros = _rows(
        evaluator.evaluate_free_agents(
            db_session, league, sources=("fantasypros",), season=SEASON
        )
    )

    assert espn["Both FA RB"]["ros_points"] == 100.0
    assert fantasypros["Both FA RB"]["ros_points"] == 200.0
    # A player the selected source does not cover has no projection at all.
    assert espn["Fp Only TE"]["has_projection"] is False
    assert espn["Fp Only TE"]["ros_points"] == 0.0
    assert fantasypros["Espn Only WR"]["has_projection"] is False


def test_no_selection_keeps_the_priority_pick(blend_league, db_session):
    league, _mine = blend_league
    rows = _rows(evaluator.evaluate_free_agents(db_session, league, season=SEASON))
    # FantasyPros wins outright; nothing is averaged.
    assert rows["Both FA RB"]["ros_points"] == 200.0
    assert rows["Espn Only WR"]["ros_points"] == 90.0
    assert rows["Fp Only TE"]["ros_points"] == 50.0


# --- weekly averaging ------------------------------------------------------


def test_week_points_average_the_selected_sources(blend_league, db_session):
    league, _mine = blend_league
    result = evaluator.evaluate_free_agents(db_session, league, sources=BOTH, season=SEASON)
    assert result["week"] == 1
    rows = _rows(result)
    assert rows["Both FA RB"]["week_points"] == 15.0
    assert rows["Espn Only WR"]["week_points"] == 9.0
    assert rows["Fp Only TE"]["week_points"] == 5.0


def test_week_points_stay_null_when_no_selected_source_has_a_row(
    blend_league, db_session
):
    league, _mine = blend_league
    espn = _rows(
        evaluator.evaluate_free_agents(db_session, league, sources=("espn",), season=SEASON)
    )
    assert espn["Fp Only TE"]["week_points"] is None
    assert espn["Fp Only TE"]["week_delta"] is None


def test_projection_week_follows_the_selected_sources(db_session):
    player = make_player(db_session, "Weekly Espn", "RB", 100)
    set_week_points(db_session, player, 10, week=2)
    db_session.commit()

    assert evaluator.current_projection_week(db_session, SEASON) == 2
    assert evaluator.current_projection_week(db_session, SEASON, ("espn",)) == 2
    # FantasyPros has nothing weekly on file, so there is no week to describe.
    assert evaluator.current_projection_week(db_session, SEASON, ("fantasypros",)) is None


# --- replacement level, VOR and deltas -------------------------------------


def test_replacement_level_is_computed_from_the_blend(db_session):
    league = make_league(
        db_session, roster_slots={"RB": 1}, num_teams=1, league_key="manual.rl"
    )
    rb1 = make_player(db_session, "RB One", "RB", 100)
    set_season_points(db_session, rb1, 300, source="fantasypros")
    make_player(db_session, "RB Two", "RB", 250)
    db_session.commit()

    # 1 RB slot * 1 team -> the replacement RB is the 2nd best.
    # Priority: [300, 250] -> 250. Blend: [250, 200] -> 200.
    assert evaluator.replacement_levels(db_session, league, SEASON)["RB"] == 250.0
    assert evaluator.replacement_levels(db_session, league, SEASON, BOTH)["RB"] == 200.0


def test_vor_subtracts_the_blended_replacement_level(blend_league, db_session):
    league, _mine = blend_league
    levels = evaluator.replacement_levels(db_session, league, SEASON, BOTH)
    rows = _rows(evaluator.evaluate_free_agents(db_session, league, sources=BOTH, season=SEASON))
    assert rows["Both FA RB"]["vor"] == round(150.0 - levels["RB"], 1)
    # ...and it is a different number than the priority view quotes.
    priority = _rows(evaluator.evaluate_free_agents(db_session, league, season=SEASON))
    assert priority["Both FA RB"]["vor"] != rows["Both FA RB"]["vor"]


def test_worst_starter_delta_uses_the_blended_starters(blend_league, db_session):
    league, _mine = blend_league
    # Priority starters: QB 400 / RB 100 / WR 80 / FLEX = TE 20, so the worst
    # FLEX-eligible starter is 20. Blended they are 300/100/70/30 -> 30.
    priority = _rows(evaluator.evaluate_free_agents(db_session, league, season=SEASON))
    blended = _rows(
        evaluator.evaluate_free_agents(db_session, league, sources=BOTH, season=SEASON)
    )
    assert priority["Both FA RB"]["my_worst_starter_delta"] == 180.0
    assert blended["Both FA RB"]["my_worst_starter_delta"] == 120.0
    # A player no selected source covers is still measured, from 0.
    assert blended["Ghost K"]["my_worst_starter_delta"] is None
    assert blended["Espn Only WR"]["my_worst_starter_delta"] == 60.0


def test_week_delta_uses_the_blended_starters(blend_league, db_session):
    league, _mine = blend_league
    # Weekly starters priority: QB 30 / RB 10 / WR 4 / TE 8 -> worst flex 4.
    # Blended: QB 25 / RB 10 / WR 5 / TE 5 -> worst flex 5.
    priority = _rows(evaluator.evaluate_free_agents(db_session, league, season=SEASON))
    blended = _rows(
        evaluator.evaluate_free_agents(db_session, league, sources=BOTH, season=SEASON)
    )
    assert priority["Both FA RB"]["week_delta"] == 14.0
    assert blended["Both FA RB"]["week_delta"] == 10.0


def test_my_players_carry_the_blended_points(blend_league, db_session):
    league, _mine = blend_league
    mine = _my_rows(evaluator.evaluate_free_agents(db_session, league, sources=BOTH, season=SEASON))
    assert mine["My QB"]["ros_points"] == 300.0
    assert mine["My QB"]["week_points"] == 25.0
    assert mine["My WR"]["ros_points"] == 70.0
    assert mine["My RB"]["ros_points"] == 100.0


# --- lineups ---------------------------------------------------------------


def test_optimal_lineup_is_built_from_the_blended_points(db_session):
    """The FLEX seat changes hands when the sources disagree about who is best."""
    league = make_league(
        db_session, roster_slots=SMALL_SLOTS, num_teams=2, league_key="manual.lineup"
    )
    mine = make_team(db_session, league, "My Squad", is_my_team=True)

    qb = make_player(db_session, "QB1", "QB", 300)
    rb = make_player(db_session, "RB1", "RB", 200)
    wr = make_player(db_session, "WR1", "WR", 150)
    # The two FLEX candidates: espn likes the RB, FantasyPros likes the TE.
    flex_rb = make_player(db_session, "Flex RB", "RB", 90)
    set_season_points(db_session, flex_rb, 10, source="fantasypros")
    flex_te = make_player(db_session, "Flex TE", "TE", 40)
    set_season_points(db_session, flex_te, 20, source="fantasypros")
    roster(db_session, league, mine, qb, rb, wr, flex_rb, flex_te)
    db_session.commit()

    def flex_seat(sources) -> str | None:
        lineup = evaluator.team_lineup(
            db_session, league, mine.id, season=SEASON, sources=sources
        )
        seat = next(entry for entry in lineup["slots"] if entry["slot"] == "FLEX")
        return seat["player"]["full_name"] if seat["player"] else None

    # Priority (FantasyPros): TE 20 beats RB 10.
    assert flex_seat(None) == "Flex TE"
    # Blended: RB (90+10)/2 = 50 beats TE (40+20)/2 = 30.
    assert flex_seat(BOTH) == "Flex RB"
    # espn alone: RB 90 beats TE 40.
    assert flex_seat(("espn",)) == "Flex RB"


def test_team_lineup_points_are_blended(blend_league, db_session):
    league, mine = blend_league
    lineup = evaluator.team_lineup(
        db_session, league, mine.id, season=SEASON, sources=BOTH
    )
    seats = {
        entry["slot"]: entry["player"]
        for entry in lineup["slots"]
        if entry["player"] is not None
    }
    assert seats["QB"]["ros_points"] == 300.0
    assert seats["QB"]["week_points"] == 25.0
    assert seats["WR"]["ros_points"] == 70.0


def test_optimal_lineup_points_accept_sources(db_session):
    league = make_league(
        db_session, roster_slots={"QB": 1, "BN": 1}, num_teams=1, league_key="manual.olp"
    )
    qb = make_player(db_session, "QB1", "QB", 200)
    set_season_points(db_session, qb, 400, source="fantasypros")
    db_session.commit()

    assert evaluator.optimal_lineup_points(db_session, league, [qb.id], SEASON) == 400.0
    assert (
        evaluator.optimal_lineup_points(db_session, league, [qb.id], SEASON, BOTH) == 300.0
    )


# --- trades ----------------------------------------------------------------


@pytest.fixture()
def blend_trade_league(db_session):
    """Two rosters where exactly one player on each side is projected twice."""
    league = make_league(db_session, roster_slots=SMALL_SLOTS, num_teams=2)
    team_a = make_team(db_session, league, "Alpha", is_my_team=True)
    team_b = make_team(db_session, league, "Bravo")

    a_qb = make_player(db_session, "A QB", "QB", 300)
    a_rb = make_player(db_session, "A RB", "RB", 100)  # fp 300 -> blend 200
    set_season_points(db_session, a_rb, 300, source="fantasypros")
    a_wr = make_player(db_session, "A WR", "WR", 50)
    roster(db_session, league, team_a, a_qb, a_rb, a_wr)

    b_qb = make_player(db_session, "B QB", "QB", 280)
    b_wr = make_player(db_session, "B WR", "WR", 200)  # fp 100 -> blend 150
    set_season_points(db_session, b_wr, 100, source="fantasypros")
    b_rb = make_player(db_session, "B RB", "RB", 40)
    roster(db_session, league, team_b, b_qb, b_wr, b_rb)

    set_value(db_session, a_rb, 5000)
    set_value(db_session, b_wr, 5000)
    db_session.commit()
    return league, {"team_a": team_a, "team_b": team_b, "a_rb": a_rb, "b_wr": b_wr}


def _trade(db_session, league, f, sources) -> dict:
    return evaluator.evaluate_trade(
        db_session,
        league,
        {"team_id": f["team_a"].id, "player_ids": [f["a_rb"].id]},
        {"team_id": f["team_b"].id, "player_ids": [f["b_wr"].id]},
        season=SEASON,
        sources=sources,
    )


def test_trade_totals_reflect_the_blend(blend_trade_league, db_session):
    league, f = blend_trade_league
    priority = _trade(db_session, league, f, None)
    blended = _trade(db_session, league, f, BOTH)

    # Priority takes FantasyPros outright: A RB 300, B WR 100.
    assert priority["sides"]["a"]["ros_points_total"] == 300.0
    assert priority["sides"]["b"]["ros_points_total"] == 100.0
    assert blended["sides"]["a"]["ros_points_total"] == 200.0
    assert blended["sides"]["b"]["ros_points_total"] == 150.0
    assert blended["sides"]["a"]["players"][0]["ppg"] == round(200 / 17, 1)
    # Market values are untouched by the projection source.
    assert blended["verdict"] == priority["verdict"] == "fair"


def test_trade_lineup_deltas_reflect_the_blend(blend_trade_league, db_session):
    league, f = blend_trade_league
    priority = _trade(db_session, league, f, None)
    blended = _trade(db_session, league, f, BOTH)

    # Priority: Alpha QB 300 + RB 300 + WR 50 = 650 before; after the swap the
    # 100-point B WR starts at WR and A WR drops to FLEX -> 450.
    assert priority["sides"]["a"]["lineup_points_before"] == 650.0
    assert priority["sides"]["a"]["lineup_points_after"] == 450.0
    assert priority["sides"]["a"]["lineup_delta"] == -200.0
    assert priority["sides"]["b"]["lineup_delta"] == 200.0

    # Blended: Alpha 300+200+50 = 550 before, 300+150+50 = 500 after.
    assert blended["sides"]["a"]["lineup_points_before"] == 550.0
    assert blended["sides"]["a"]["lineup_points_after"] == 500.0
    assert blended["sides"]["a"]["lineup_delta"] == -50.0
    assert blended["sides"]["b"]["lineup_delta"] == 50.0
    assert any("Alpha's optimal lineup changes by -50.0" in n for n in blended["notes"])


def test_trade_missing_projection_note_follows_the_selection(
    blend_trade_league, db_session
):
    league, f = blend_trade_league
    # espn alone covers everyone; FantasyPros only covers the two blended players.
    result = evaluator.evaluate_trade(
        db_session,
        league,
        {"team_id": f["team_a"].id, "player_ids": [f["a_rb"].id]},
        {"team_id": f["team_b"].id, "player_ids": [f["b_wr"].id]},
        season=SEASON,
        sources=("fantasypros",),
    )
    assert result["sides"]["a"]["ros_points_total"] == 300.0
    # Alpha's QB and WR are espn-only, so FantasyPros alone cannot start them.
    assert result["sides"]["a"]["lineup_points_before"] == 300.0
    assert not any("No rest-of-season projection" in n for n in result["notes"])


# --- backward compatibility ------------------------------------------------


def test_sources_none_matches_the_pre_existing_behaviour(week_league, db_session):
    """The historical scenario answers identically with and without the arg."""
    league, _f = week_league
    explicit = evaluator.evaluate_free_agents(db_session, league, season=SEASON, sources=None)
    implicit = evaluator.evaluate_free_agents(db_session, league, season=SEASON)
    assert explicit == implicit

    rows = _rows(implicit)
    # The numbers test_evaluator pins for this fixture, unchanged.
    assert implicit["week"] == 1
    assert rows["Hot RB"]["ros_points"] == 100.0
    assert rows["Hot RB"]["week_points"] == 9.0
    assert rows["Hot RB"]["week_delta"] == 5.0
    assert rows["Hot RB"]["my_worst_starter_delta"] == 40.0
    assert rows["Steady WR"]["week_points"] is None


def test_selecting_every_source_matches_priority_on_single_source_data(
    week_league, db_session
):
    """Averaging one row is that row: a one-source league sees no change."""
    league, _f = week_league
    assert evaluator.evaluate_free_agents(
        db_session, league, season=SEASON, sources=BOTH
    ) == evaluator.evaluate_free_agents(db_session, league, season=SEASON)


def test_empty_source_selection_is_treated_as_no_selection(blend_league, db_session):
    league, _mine = blend_league
    assert evaluator.evaluate_free_agents(
        db_session, league, season=SEASON, sources=()
    ) == evaluator.evaluate_free_agents(db_session, league, season=SEASON)


# --- projection_sources ----------------------------------------------------


def test_projection_sources_reports_freshness_per_source(db_session):
    player = make_player(db_session, "Espn Guy", "RB", 100)
    set_week_points(db_session, player, 10, week=3)
    db_session.commit()

    result = evaluator.projection_sources(db_session, SEASON)
    assert result["week"] == 3
    assert [row["source"] for row in result["sources"]] == ["fantasypros", "espn"]

    by_source = {row["source"]: row for row in result["sources"]}
    assert by_source["espn"]["season_updated_at"] is not None
    assert by_source["espn"]["week_updated_at"] is not None
    # FantasyPros has nothing on file yet but is still offered as a choice.
    assert by_source["fantasypros"]["season_updated_at"] is None
    assert by_source["fantasypros"]["week_updated_at"] is None


def test_projection_sources_week_timestamp_is_null_without_weekly_rows(db_session):
    make_player(db_session, "Season Only", "RB", 100)
    db_session.commit()

    result = evaluator.projection_sources(db_session, SEASON)
    assert result["week"] is None
    by_source = {row["source"]: row for row in result["sources"]}
    assert by_source["espn"]["season_updated_at"] is not None
    assert by_source["espn"]["week_updated_at"] is None


def test_projection_sources_ignores_other_seasons(db_session):
    player = make_player(db_session, "Last Year", "RB", None)
    db_session.add(
        Projection(
            player_id=player.id,
            source="espn",
            season=SEASON - 1,
            week=None,
            stat_json={"rush_yds": 1000},
            fetched_at=_utcnow(),
        )
    )
    db_session.commit()

    result = evaluator.projection_sources(db_session, SEASON)
    assert all(row["season_updated_at"] is None for row in result["sources"])


# --- endpoints -------------------------------------------------------------


def test_free_agents_endpoint_accepts_a_source_selection(client, blend_league):
    both = client.get(
        "/api/leagues/manual.1/evaluate/free-agents?sources=fantasypros,espn"
    ).json()
    espn = client.get(
        "/api/leagues/manual.1/evaluate/free-agents?sources=ESPN"
    ).json()
    default = client.get("/api/leagues/manual.1/evaluate/free-agents").json()

    assert _rows(both)["Both FA RB"]["ros_points"] == 150.0
    assert _rows(espn)["Both FA RB"]["ros_points"] == 100.0
    assert _rows(default)["Both FA RB"]["ros_points"] == 200.0
    assert _my_rows(both)["My QB"]["ros_points"] == 300.0


def test_free_agents_endpoint_rejects_an_unknown_source(client, blend_league):
    response = client.get(
        "/api/leagues/manual.1/evaluate/free-agents?sources=fantasypros,nfl.com"
    )
    assert response.status_code == 400
    assert "nfl.com" in response.json()["detail"]


def test_lineup_endpoint_accepts_a_source_selection(client, blend_league):
    _league, mine = blend_league
    body = client.get(
        f"/api/leagues/manual.1/evaluate/teams/{mine.id}/lineup?sources=fantasypros,espn"
    ).json()
    qb = next(entry for entry in body["slots"] if entry["slot"] == "QB")
    assert qb["player"]["ros_points"] == 300.0
    assert qb["player"]["week_points"] == 25.0

    default = client.get(
        f"/api/leagues/manual.1/evaluate/teams/{mine.id}/lineup"
    ).json()
    default_qb = next(entry for entry in default["slots"] if entry["slot"] == "QB")
    assert default_qb["player"]["ros_points"] == 400.0


def test_lineup_endpoint_rejects_an_unknown_source(client, blend_league):
    _league, mine = blend_league
    response = client.get(
        f"/api/leagues/manual.1/evaluate/teams/{mine.id}/lineup?sources=yahoo"
    )
    assert response.status_code == 400
    assert "yahoo" in response.json()["detail"]


def test_trade_endpoint_accepts_a_source_selection(client, blend_trade_league):
    _league, f = blend_trade_league
    body = {
        "side_a": {"team_id": f["team_a"].id, "player_ids": [f["a_rb"].id]},
        "side_b": {"team_id": f["team_b"].id, "player_ids": [f["b_wr"].id]},
    }
    default = client.post("/api/leagues/manual.1/evaluate/trade", json=body).json()
    blended = client.post(
        "/api/leagues/manual.1/evaluate/trade",
        json={**body, "sources": ["espn", "fantasypros"]},
    ).json()

    assert default["sides"]["a"]["ros_points_total"] == 300.0
    assert blended["sides"]["a"]["ros_points_total"] == 200.0
    assert blended["sides"]["a"]["lineup_delta"] == -50.0


def test_trade_endpoint_rejects_an_unknown_source(client, blend_trade_league):
    _league, f = blend_trade_league
    response = client.post(
        "/api/leagues/manual.1/evaluate/trade",
        json={
            "side_a": {"team_id": f["team_a"].id, "player_ids": [f["a_rb"].id]},
            "side_b": {"team_id": f["team_b"].id, "player_ids": [f["b_wr"].id]},
            "sources": ["madden"],
        },
    )
    assert response.status_code == 400
    assert "madden" in response.json()["detail"]


def test_trade_endpoint_empty_source_list_is_the_default(client, blend_trade_league):
    _league, f = blend_trade_league
    body = {
        "side_a": {"team_id": f["team_a"].id, "player_ids": [f["a_rb"].id]},
        "side_b": {"team_id": f["team_b"].id, "player_ids": [f["b_wr"].id]},
    }
    response = client.post(
        "/api/leagues/manual.1/evaluate/trade", json={**body, "sources": []}
    )
    assert response.status_code == 200
    assert response.json()["sides"]["a"]["ros_points_total"] == 300.0


def test_projection_sources_endpoint(client, db_session):
    player = make_player(db_session, "Espn Guy", "RB", 100)
    set_week_points(db_session, player, 10, week=2)
    db_session.commit()

    response = client.get("/api/projections/sources")
    assert response.status_code == 200

    body = response.json()
    assert body["week"] == 2
    assert [row["source"] for row in body["sources"]] == ["fantasypros", "espn"]

    by_source = {row["source"]: row for row in body["sources"]}
    assert by_source["espn"]["season_updated_at"] is not None
    assert by_source["espn"]["week_updated_at"] is not None
    assert by_source["fantasypros"]["season_updated_at"] is None
    assert by_source["fantasypros"]["week_updated_at"] is None


def test_projection_sources_endpoint_with_no_projections_at_all(client, db_session):
    body = client.get("/api/projections/sources").json()
    assert body["week"] is None
    assert body["sources"] == [
        {"source": "fantasypros", "season_updated_at": None, "week_updated_at": None},
        {"source": "espn", "season_updated_at": None, "week_updated_at": None},
    ]
