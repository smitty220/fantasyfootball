"""Yahoo leagues default to the lineup the owner actually set in Yahoo.

Covers :func:`app.services.evaluator.yahoo_lineup`, the ``lineup`` query
param on the team-lineup endpoint, the fall-back to the optimal lineup when
nothing is synced, slot mapping (``W/R/T`` -> FLEX, ``BN``/``IR`` -> bench)
and the dashboard alerts being judged on the actual lineup.
"""

from __future__ import annotations

import pytest

from app.models import NflGame, RosterSlot
from app.services import evaluator
from tests.test_evaluator import (
    SEASON,
    make_league,
    make_player,
    make_team,
    roster,
    set_week_points,
)

#: Yahoo's own slot spellings, as the sync stores them in settings.
YAHOO_SLOTS = {"QB": 1, "RB": 1, "WR": 1, "W/R/T": 1, "BN": 2, "IR": 1}
YAHOO_KEY = "470.l.1"
CURRENT_WEEK = 5


def set_yahoo_slots(db, team, week: int, assignments: dict) -> None:
    for player, slot in assignments.items():
        db.add(
            RosterSlot(
                team_id=team.id,
                week=week,
                player_id=player.id,
                selected_position=slot,
            )
        )


@pytest.fixture()
def yahoo_seed(db_session):
    """A Yahoo league whose owner set a deliberately sub-optimal lineup.

    Yahoo lineup: QB Star at QB, RB Bye (on bye, worse than RB Good) at RB,
    WR Ok at WR, TE Flex at W/R/T; RB Good on the bench (BN), RB Hurt on IR.
    The optimal lineup would start RB Good at RB and move RB Bye to FLEX.
    """
    league = make_league(
        db_session,
        roster_slots=YAHOO_SLOTS,
        source="yahoo",
        league_key=YAHOO_KEY,
    )
    league.current_week = CURRENT_WEEK
    team = make_team(db_session, league, "Mine", is_my_team=True)

    # Week-1 game: everyone on SEA plays, RB Bye's team (SF) does not.
    db_session.add(NflGame(season=SEASON, week=1, home_team="SEA", away_team="GB"))

    qb = make_player(db_session, "QB Star", "QB", 300, nfl_team="SEA")
    rb_bye = make_player(db_session, "RB Bye", "RB", 180, nfl_team="SF")
    rb_good = make_player(db_session, "RB Good", "RB", 200, nfl_team="SEA")
    wr = make_player(db_session, "WR Ok", "WR", 150, nfl_team="SEA")
    te = make_player(db_session, "TE Flex", "TE", 90, nfl_team="SEA")
    rb_hurt = make_player(db_session, "RB Hurt", "RB", 250, nfl_team="SEA")
    rb_hurt.injury_status = "IR"
    roster(db_session, league, team, qb, rb_bye, rb_good, wr, te, rb_hurt)

    set_week_points(db_session, qb, 22)
    set_week_points(db_session, rb_bye, 0)
    set_week_points(db_session, rb_good, 15)
    set_week_points(db_session, wr, 12)
    set_week_points(db_session, te, 6)
    set_week_points(db_session, rb_hurt, 40)  # on IR: never a swap suggestion

    set_yahoo_slots(
        db_session,
        team,
        CURRENT_WEEK,
        {
            qb: "QB",
            rb_bye: "RB",
            wr: "WR",
            te: "W/R/T",
            rb_good: "BN",
            rb_hurt: "IR",
        },
    )
    db_session.commit()
    return {"league": league, "team": team}


def _url(team_id: int, query: str = "") -> str:
    return f"/api/leagues/{YAHOO_KEY}/evaluate/teams/{team_id}/lineup{query}"


def _starters(body: dict) -> list[tuple[str, str | None]]:
    return [
        (entry["slot"], entry["player"]["full_name"] if entry["player"] else None)
        for entry in body["slots"]
    ]


def test_yahoo_lineup_is_the_default(client, yahoo_seed):
    body = client.get(_url(yahoo_seed["team"].id)).json()
    assert body["source"] == "yahoo"
    assert _starters(body) == [
        ("QB", "QB Star"),
        ("RB", "RB Bye"),
        ("WR", "WR Ok"),
        ("FLEX", "TE Flex"),
    ]
    bench = {row["full_name"]: row for row in body["bench"]}
    assert set(bench) == {"RB Good", "RB Hurt"}
    assert bench["RB Hurt"]["reserve"] is True
    assert bench["RB Good"]["reserve"] is False
    assert all(
        entry["player"]["reserve"] is False for entry in body["slots"] if entry["player"]
    )


def test_lineup_optimal_param_forces_computed_lineup(client, yahoo_seed):
    team_id = yahoo_seed["team"].id
    body = client.get(_url(team_id, "?lineup=optimal")).json()
    assert body["source"] == "auto"
    names = {name for _slot, name in _starters(body)}
    # This week's best lineup: the IR'd RB Hurt (best ROS RB) sits even though
    # Yahoo's slot is the only sign he's out.
    assert "RB Hurt" not in names
    assert all(row["reserve"] is False for row in body["bench"])


def test_lineup_param_rejects_unknown_mode(client, yahoo_seed):
    response = client.get(_url(yahoo_seed["team"].id, "?lineup=bogus"))
    assert response.status_code == 422


def test_falls_back_to_optimal_when_current_week_not_synced(
    client, db_session, yahoo_seed
):
    league = yahoo_seed["league"]
    league.current_week = CURRENT_WEEK + 1  # rows exist only for an older week
    db_session.commit()
    body = client.get(_url(yahoo_seed["team"].id)).json()
    assert body["source"] == "auto"


def test_falls_back_to_optimal_with_no_rows(client, db_session):
    league = make_league(
        db_session, roster_slots=YAHOO_SLOTS, source="yahoo", league_key=YAHOO_KEY
    )
    league.current_week = 3
    team = make_team(db_session, league, "Empty")
    qb = make_player(db_session, "Lone QB", "QB", 100)
    roster(db_session, league, team, qb)
    db_session.commit()

    body = client.get(_url(team.id)).json()
    assert body["source"] == "auto"
    assert _starters(body)[0] == ("QB", "Lone QB")


def test_latest_synced_week_used_when_league_has_no_current_week(
    db_session, yahoo_seed
):
    league = yahoo_seed["league"]
    league.current_week = None
    db_session.commit()
    result = evaluator.team_lineup(db_session, league, yahoo_seed["team"].id)
    assert result["source"] == "yahoo"


def test_empty_yahoo_seat_reads_back_null(client, db_session):
    league = make_league(
        db_session, roster_slots=YAHOO_SLOTS, source="yahoo", league_key=YAHOO_KEY
    )
    league.current_week = 2
    team = make_team(db_session, league, "Gappy")
    qb = make_player(db_session, "Benched QB", "QB", 300)
    wr = make_player(db_session, "Real WR", "WR", 100)
    roster(db_session, league, team, qb, wr)
    set_yahoo_slots(db_session, team, 2, {qb: "BN", wr: "WR"})
    db_session.commit()

    body = client.get(_url(team.id)).json()
    assert body["source"] == "yahoo"
    assert _starters(body) == [
        ("QB", None),
        ("RB", None),
        ("WR", "Real WR"),
        ("FLEX", None),
    ]
    assert [row["full_name"] for row in body["bench"]] == ["Benched QB"]


def test_yahoo_lineup_slot_mapping(db_session, yahoo_seed):
    assignments, reserve = evaluator.yahoo_lineup(
        db_session, yahoo_seed["league"], yahoo_seed["team"].id
    )
    assert sorted(assignments.values()) == ["BN", "FLEX", "IR", "QB", "RB", "WR"]
    assert len(reserve) == 1


@pytest.mark.parametrize(
    "slot, reserve",
    [("IR", True), ("IR+", True), ("NA", True), ("BN", False), ("W/R/T", False)],
)
def test_reserve_slot_detection(slot, reserve):
    assert evaluator._is_reserve_slot(slot) is reserve


def test_manual_league_ignores_synced_week_rows(client, db_session):
    """A manual league never reads week >= 1 rows; behaviour is unchanged."""
    league = make_league(db_session, roster_slots=YAHOO_SLOTS, league_key="manual.1")
    league.current_week = 4
    team = make_team(db_session, league, "Manual")
    qb = make_player(db_session, "QB M", "QB", 300)
    roster(db_session, league, team, qb)
    set_yahoo_slots(db_session, team, 4, {qb: "BN"})
    db_session.commit()

    body = client.get(f"/api/leagues/manual.1/evaluate/teams/{team.id}/lineup").json()
    assert body["source"] == "auto"
    assert _starters(body)[0] == ("QB", "QB M")


def test_dashboard_alerts_judge_actual_yahoo_lineup(client, yahoo_seed):
    body = client.get("/api/dashboard").json()
    (league,) = body["leagues"]
    alerts = league["alerts"]

    # RB Bye starts in Yahoo; the optimal lineup would have benched him for
    # RB Hurt, so this alert only exists because the actual lineup is used.
    assert alerts["bye_starters"] == [{"name": "RB Bye", "slot": "RB"}]
    assert alerts["injured_starters"] == []
    # RB Good (BN, 15) beats RB Bye (0) at RB and TE Flex (6) at FLEX. RB Hurt
    # (40, but on IR) is never suggested.
    assert alerts["bench_beats_starter"] == [
        {"starter": "RB Bye", "slot": "RB", "bench": "RB Good", "bench_week_points": 15.0},
        {"starter": "TE Flex", "slot": "FLEX", "bench": "RB Good", "bench_week_points": 15.0},
    ]
    # Actual starters: 22 + 0 + 12 + 6.
    assert league["my_lineup_week_points"] == 40.0


def test_free_agent_eval_uses_optimal_starters_for_yahoo(client, db_session, yahoo_seed):
    result = evaluator.evaluate_free_agents(db_session, yahoo_seed["league"])
    fa_starters = {
        row["full_name"]: row["starter_slot"]
        for row in result["my_players"]
        if row["is_starter"]
    }
    optimal = client.get(_url(yahoo_seed["team"].id, "?lineup=optimal")).json()
    assert fa_starters == {
        entry["player"]["full_name"]: entry["slot"]
        for entry in optimal["slots"]
        if entry["player"]
    }
    # This week's best: the on-bye RB sits and TE Flex (6 pts) takes FLEX.
    assert "RB Bye" not in fa_starters
