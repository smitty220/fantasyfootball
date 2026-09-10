"""Projection-source grading: /api/leagues/{league_key}/accuracy.

The scenario below is small enough to grade by hand, which is the point: every
expected mae/bias in this file is worked out in a comment from the seeded stat
lines, so a change in the grading definition has to be argued for rather than
re-blessed.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models import ActualStat, Player, Projection
from tests.test_evaluator import SEASON, SMALL_SLOTS, make_league


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def make_player(db, name: str, position: str | None) -> Player:
    player = Player(full_name=name, position=position, nfl_team="SF")
    db.add(player)
    db.flush()
    return player


def set_actual(db, player: Player, week: int, **stats) -> None:
    db.add(
        ActualStat(
            player_id=player.id,
            season=SEASON,
            week=week,
            stat_json=stats,
            fetched_at=_utcnow(),
        )
    )


def set_projection(db, player: Player, source: str, week: int | None, **stats) -> None:
    db.add(
        Projection(
            player_id=player.id,
            source=source,
            season=SEASON,
            week=week,
            stat_json=stats,
            fetched_at=_utcnow(),
        )
    )


@pytest.fixture()
def graded(db_session):
    """Two players x two weeks x two sources, plus rows that must be ignored.

    Actual points, scored under each league's rules (standard: rec=0,
    full PPR: rec=1; rush/rec yards 0.1, rush/rec TD 6):

    ================= ============== ===========
    player-week       standard       full PPR
    ================= ============== ===========
    WR wk1  5/50       5.0            10.0
    RB wk1  100yd+TD  16.0            16.0
    WR wk2  3/20       2.0             5.0
    RB wk2  50yd       5.0             5.0
    ================= ============== ===========
    """
    ppr = make_league(
        db_session, roster_slots=SMALL_SLOTS, league_key="manual.ppr", preset="full_ppr"
    )
    standard = make_league(
        db_session,
        roster_slots=SMALL_SLOTS,
        league_key="manual.std",
        preset="standard",
    )

    wr = make_player(db_session, "Graded WR", "WR")
    rb = make_player(db_session, "Graded RB", "RB")

    set_actual(db_session, wr, 1, rec=5, rec_yds=50)
    set_actual(db_session, rb, 1, rush_yds=100, rush_td=1)
    set_actual(db_session, wr, 2, rec=3, rec_yds=20)
    set_actual(db_session, rb, 2, rush_yds=50)

    # fantasypros: a full slate. std points 6/17 (wk1), 3/4 (wk2);
    # ppr points 12/17 (wk1), 4/4 (wk2).
    set_projection(db_session, wr, "fantasypros", 1, rec=6, rec_yds=60)
    set_projection(db_session, rb, "fantasypros", 1, rush_yds=110, rush_td=1)
    set_projection(db_session, wr, "fantasypros", 2, rec=1, rec_yds=30)
    set_projection(db_session, rb, "fantasypros", 2, rush_yds=40)

    # espn: no week-2 RB projection at all -- that player-week simply isn't
    # graded for espn (n=3), rather than counting as a zero.
    set_projection(db_session, wr, "espn", 1, rec=4, rec_yds=40)
    set_projection(db_session, rb, "espn", 1, rush_yds=80)
    set_projection(db_session, wr, "espn", 2, rec=5, rec_yds=60)

    # Ignored: a season/ROS row (week NULL is a different horizon)...
    set_projection(db_session, wr, "fantasypros", None, rec=100, rec_yds=1200)
    # ...a source we don't grade...
    set_projection(db_session, wr, "sleeper", 1, rec=99, rec_yds=999)
    # ...and a player with no position at all, actuals and projections alike.
    nobody = make_player(db_session, "Position Unknown", None)
    set_actual(db_session, nobody, 1, rush_yds=100)
    set_projection(db_session, nobody, "fantasypros", 1, rush_yds=500)

    db_session.commit()
    return {"ppr": ppr, "standard": standard, "wr": wr, "rb": rb}


def _source(body: dict, name: str) -> dict:
    return next(row for row in body["sources"] if row["source"] == name)


def test_unknown_league_is_404(client):
    response = client.get("/api/leagues/nope.1/accuracy")

    assert response.status_code == 404
    assert "nope.1" in response.json()["detail"]


def test_empty_state_reports_no_weeks_and_zeroed_sources(client, db_session):
    make_league(db_session, roster_slots=SMALL_SLOTS)
    db_session.commit()

    body = client.get("/api/leagues/manual.1/accuracy").json()

    assert body["league_key"] == "manual.1"
    assert body["season"] == SEASON
    assert body["weeks"] == []
    assert [row["source"] for row in body["sources"]] == ["fantasypros", "espn"]
    for row in body["sources"]:
        assert row["overall"] == {"n": 0, "mae": 0.0, "bias": 0.0}
        assert row["by_week"] == []
        assert row["by_position"] == []


def test_standard_league_grading_matches_hand_computed_errors(client, graded):
    body = client.get("/api/leagues/manual.std/accuracy").json()

    assert body["weeks"] == [1, 2]

    # fantasypros errors (projected - actual, standard scoring):
    #   wk1 WR 6.0-5.0=+1, wk1 RB 17.0-16.0=+1
    #   wk2 WR 3.0-2.0=+1, wk2 RB 4.0-5.0=-1
    fp = _source(body, "fantasypros")
    assert fp["overall"] == {"n": 4, "mae": 1.0, "bias": 0.5}
    assert fp["by_week"] == [
        {"week": 1, "n": 2, "mae": 1.0, "bias": 1.0},
        {"week": 2, "n": 2, "mae": 1.0, "bias": 0.0},
    ]
    assert fp["by_position"] == [
        {"position": "RB", "n": 2, "mae": 1.0, "bias": 0.0},
        {"position": "WR", "n": 2, "mae": 1.0, "bias": 1.0},
    ]

    # espn errors: wk1 WR 4.0-5.0=-1, wk1 RB 8.0-16.0=-8, wk2 WR 6.0-2.0=+4.
    # mae = 13/3 = 4.33, bias = -5/3 = -1.67 (it underprojects on balance).
    espn = _source(body, "espn")
    assert espn["overall"] == {"n": 3, "mae": 4.33, "bias": -1.67}
    assert espn["by_week"] == [
        {"week": 1, "n": 2, "mae": 4.5, "bias": -4.5},
        {"week": 2, "n": 1, "mae": 4.0, "bias": 4.0},
    ]


def test_ppr_league_grades_the_same_rows_differently(client, graded):
    """Same projections, same actuals, different league -> different numbers.

    Receptions are worth a point here, so every receiving miss counts for more:
    fantasypros errors become wk1 WR 12.0-10.0=+2, wk1 RB +1,
    wk2 WR 4.0-5.0=-1, wk2 RB -1 -> mae 5/4 = 1.25, bias 1/4 = 0.25
    (versus mae 1.0 / bias 0.5 in the standard league).
    """
    body = client.get("/api/leagues/manual.ppr/accuracy").json()

    fp = _source(body, "fantasypros")
    assert fp["overall"] == {"n": 4, "mae": 1.25, "bias": 0.25}
    assert fp["by_position"] == [
        {"position": "RB", "n": 2, "mae": 1.0, "bias": 0.0},
        {"position": "WR", "n": 2, "mae": 1.5, "bias": 0.5},
    ]

    # espn: wk1 WR 8.0-10.0=-2, wk1 RB -8, wk2 WR 11.0-5.0=+6.
    espn = _source(body, "espn")
    assert espn["overall"] == {"n": 3, "mae": 5.33, "bias": -1.33}


def test_grading_ignores_ros_rows_unknown_sources_and_positionless_players(
    client, graded, db_session
):
    """The seeded traps would each blow the counts up if they were graded.

    The ROS row (1200 receiving yards) and the positionless player's 500-yard
    projection would dominate any average they landed in; ``sleeper`` isn't a
    source we grade at all.
    """
    body = client.get("/api/leagues/manual.std/accuracy").json()

    assert [row["source"] for row in body["sources"]] == ["fantasypros", "espn"]
    assert _source(body, "fantasypros")["overall"]["n"] == 4
    assert _source(body, "fantasypros")["overall"]["mae"] == 1.0
    positions = {
        row["position"] for row in _source(body, "fantasypros")["by_position"]
    }
    assert positions == {"WR", "RB"}


def test_weeks_lists_every_week_with_actuals_even_when_ungraded(
    client, graded, db_session
):
    """A week with actuals but no projections still shows up in ``weeks``.

    ``weeks`` describes what we have graded *against*, so it must not shrink
    just because a source was silent that week -- that gap is exactly what the
    per-week ``n`` is there to show.
    """
    set_actual(db_session, graded["wr"], 5, rec=2, rec_yds=15)
    db_session.commit()

    body = client.get("/api/leagues/manual.std/accuracy").json()

    assert body["weeks"] == [1, 2, 5]
    assert [row["week"] for row in _source(body, "fantasypros")["by_week"]] == [1, 2]


def test_kicker_actuals_grade_under_the_leagues_kicking_rules(client, db_session):
    """Kickers are ingested from the same nflverse file, so they grade too."""
    make_league(db_session, roster_slots=SMALL_SLOTS)
    kicker = make_player(db_session, "Graded K", "K")
    # 2 FGs (30-39 and 50+) + 3 XP = 3 + 5 + 3 = 11.0 actual points.
    set_actual(db_session, kicker, 1, fg_30_39=1, fg_50_plus=1, xp_made=3)
    # Projected 2 short FGs + 3 XP = 3 + 3 + 3 = 9.0 -> error -2.0.
    set_projection(db_session, kicker, "fantasypros", 1, fg_30_39=2, xp_made=3)
    db_session.commit()

    body = client.get("/api/leagues/manual.1/accuracy").json()

    fp = _source(body, "fantasypros")
    assert fp["overall"] == {"n": 1, "mae": 2.0, "bias": -2.0}
    assert fp["by_position"] == [{"position": "K", "n": 1, "mae": 2.0, "bias": -2.0}]
