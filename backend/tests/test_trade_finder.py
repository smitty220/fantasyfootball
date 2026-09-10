"""Trade finder: candidate generation, filtering, de-noising and the route.

The league below is engineered rather than random: every roster exists to make
one rule observable in isolation, so a failure names the rule it broke.

* **Bravo** is the obvious win-win -- my WR surplus against their RB surplus --
  and also carries the K/DEF trap: a defense swap that clears every *numeric*
  filter and must still never be suggested, because kickers and defenses are
  not tradeable at all.
* **Charlie** would hand me a huge lineup gain and would gain too, but only
  through wildly lopsided market values, so nothing with Charlie survives.
* **Delta** offers a perfectly fair, hugely positive-for-me deal that guts
  their own lineup; no rational manager accepts it, so it is filtered out.
* **Echo** is priced so that no single player of mine is within range of their
  one stud, which leaves a two-for-one as the only shape that works -- and
  supplies a second, strictly worse package for the same return, which the
  dominance pruning has to drop.
"""

from __future__ import annotations

import time

import pytest

from app.services import evaluator, trade_finder
from tests.test_evaluator import (
    SEASON,
    make_league,
    make_player,
    make_team,
    roster,
    set_value,
)

#: One QB, two RB, two WR, a kicker, a defense and a four-man bench.
TF_SLOTS = {"QB": 1, "RB": 2, "WR": 2, "K": 1, "DEF": 1, "BN": 4}


def _add(db, league, team, name, position, points, value):
    player = make_player(db, name, position, points)
    roster(db, league, team, player)
    if value is not None:
        set_value(db, player, value)
    return player


def build_league(db):
    """The engineered five-team league described in the module docstring."""
    league = make_league(db, roster_slots=TF_SLOTS, num_teams=5)
    f: dict = {}

    alpha = make_team(db, league, "Alpha", is_my_team=True)
    f["alpha"] = alpha
    # Strong at WR (three startable-quality bodies for two seats), a hole at
    # RB2, and a deliberately weak defense.
    f["my_qb"] = _add(db, league, alpha, "My QB", "QB", 250, 4000)
    f["my_rb1"] = _add(db, league, alpha, "My RB1", "RB", 180, 6600)
    f["my_rb2"] = _add(db, league, alpha, "My RB2", "RB", 60, 300)
    f["my_wr1"] = _add(db, league, alpha, "My WR1", "WR", 200, 5000)
    f["my_wr2"] = _add(db, league, alpha, "My WR2", "WR", 190, 4800)
    f["my_wr3"] = _add(db, league, alpha, "My WR3", "WR", 185, 3000)
    f["my_k"] = _add(db, league, alpha, "My K", "K", 100, 100)
    f["my_def"] = _add(db, league, alpha, "My DEF", "DEF", 60, 500)

    bravo = make_team(db, league, "Bravo")
    f["bravo"] = bravo
    f["b_qb"] = _add(db, league, bravo, "B QB", "QB", 240, 3800)
    f["b_rb1"] = _add(db, league, bravo, "B RB1", "RB", 195, 5200)
    f["b_rb2"] = _add(db, league, bravo, "B RB2", "RB", 190, 5100)
    f["b_rb3"] = _add(db, league, bravo, "B RB3", "RB", 185, 3000)
    f["b_wr1"] = _add(db, league, bravo, "B WR1", "WR", 175, 4000)
    f["b_wr2"] = _add(db, league, bravo, "B WR2", "WR", 55, 400)
    f["b_k"] = _add(db, league, bravo, "B K", "K", 100, 100)
    f["b_def1"] = _add(db, league, bravo, "B DEF1", "DEF", 150, 600)
    # The trap: swapping this benched defense for mine is worth +85 to my
    # lineup, costs Bravo nothing and is priced identically to mine.
    f["b_def2"] = _add(db, league, bravo, "B DEF2", "DEF", 145, 500)

    charlie = make_team(db, league, "Charlie")
    f["charlie"] = charlie
    f["c_qb"] = _add(db, league, charlie, "C QB", "QB", 240, 3800)
    f["c_rb1"] = _add(db, league, charlie, "C RB1", "RB", 250, 1000)
    f["c_rb2"] = _add(db, league, charlie, "C RB2", "RB", 240, 900)
    f["c_rb3"] = _add(db, league, charlie, "C RB3", "RB", 230, 800)
    f["c_wr1"] = _add(db, league, charlie, "C WR1", "WR", 100, 900)
    f["c_wr2"] = _add(db, league, charlie, "C WR2", "WR", 90, 900)
    f["c_k"] = _add(db, league, charlie, "C K", "K", 100, 100)
    f["c_def"] = _add(db, league, charlie, "C DEF", "DEF", 100, 500)

    delta = make_team(db, league, "Delta")
    f["delta"] = delta
    f["d_qb"] = _add(db, league, delta, "D QB", "QB", 240, 3800)
    f["d_rb1"] = _add(db, league, delta, "D RB1", "RB", 240, 3000)
    f["d_rb2"] = _add(db, league, delta, "D RB2", "RB", 200, 2900)
    f["d_wr1"] = _add(db, league, delta, "D WR1", "WR", 195, 4900)
    f["d_wr2"] = _add(db, league, delta, "D WR2", "WR", 190, 4800)
    f["d_k"] = _add(db, league, delta, "D K", "K", 100, 100)
    f["d_def"] = _add(db, league, delta, "D DEF", "DEF", 100, 500)

    echo = make_team(db, league, "Echo")
    f["echo"] = echo
    f["e_qb"] = _add(db, league, echo, "E QB", "QB", 240, 3800)
    f["e_rb1"] = _add(db, league, echo, "E RB1", "RB", 280, 8000)
    f["e_rb2"] = _add(db, league, echo, "E RB2", "RB", 50, 200)
    f["e_wr1"] = _add(db, league, echo, "E WR1", "WR", 40, 200)
    f["e_wr2"] = _add(db, league, echo, "E WR2", "WR", 35, 200)
    f["e_k"] = _add(db, league, echo, "E K", "K", 100, 100)
    f["e_def"] = _add(db, league, echo, "E DEF", "DEF", 100, 500)

    db.commit()
    return league, f


@pytest.fixture()
def tf_league(db_session):
    return build_league(db_session)


# --- reading the payload ---------------------------------------------------


def _deals(result: dict) -> set[tuple[str, tuple[str, ...], tuple[str, ...]]]:
    """Every suggestion as (opponent, sent names, received names)."""
    return {
        (
            row["opponent"]["name"],
            tuple(p["full_name"] for p in row["sends"]),
            tuple(p["full_name"] for p in row["receives"]),
        )
        for row in result["suggestions"]
    }


def _names(result: dict) -> set[str]:
    """Every player named anywhere in the payload."""
    return {
        player["full_name"]
        for row in result["suggestions"]
        for player in row["sends"] + row["receives"]
    }


def _for(result: dict, opponent: str) -> list[dict]:
    return [
        row for row in result["suggestions"] if row["opponent"]["name"] == opponent
    ]


def _find(result: dict, sends: tuple[str, ...], receives: tuple[str, ...]) -> dict | None:
    for row in result["suggestions"]:
        if (
            tuple(p["full_name"] for p in row["sends"]) == sends
            and tuple(p["full_name"] for p in row["receives"]) == receives
        ):
            return row
    return None


# --- the happy path --------------------------------------------------------


def test_my_team_is_reported(tf_league, db_session):
    league, f = tf_league
    result = trade_finder.find_trades(db_session, league, season=SEASON)
    assert result["my_team"] == {"id": f["alpha"].id, "name": "Alpha"}


def test_the_obvious_win_win_is_suggested(tf_league, db_session):
    """My spare WR3 for Bravo's spare RB3: same price, both lineups improve."""
    league, _f = tf_league
    result = trade_finder.find_trades(db_session, league, limit=25, season=SEASON)

    deal = _find(result, ("My WR3",), ("B RB3",))
    assert deal is not None
    assert deal["opponent"]["name"] == "Bravo"
    assert deal["kind"] == "1for1"
    # My RB2 seat goes from 60 to 185; Bravo's WR2 seat from 55 to 185.
    assert deal["my_lineup_delta"] == 125.0
    assert deal["opp_lineup_delta"] == 130.0
    assert deal["value_margin_pct"] == 0.0


def test_suggestion_rows_carry_player_detail(tf_league, db_session):
    league, f = tf_league
    result = trade_finder.find_trades(db_session, league, limit=25, season=SEASON)

    deal = _find(result, ("My WR3",), ("B RB3",))
    assert deal["sends"] == [
        {
            "player_id": f["my_wr3"].id,
            "full_name": "My WR3",
            "position": "WR",
            "ros_points": 185.0,
            "value": 3000.0,
        }
    ]
    assert deal["receives"][0]["position"] == "RB"
    assert deal["receives"][0]["value"] == 3000.0


def test_every_suggestion_helps_me_and_is_near_fair(tf_league, db_session):
    league, _f = tf_league
    result = trade_finder.find_trades(db_session, league, limit=25, season=SEASON)

    assert result["suggestions"]
    for row in result["suggestions"]:
        assert row["my_lineup_delta"] >= trade_finder.MIN_MY_DELTA
        assert row["opp_lineup_delta"] >= trade_finder.MIN_OPP_DELTA
        assert row["value_margin_pct"] <= (
            evaluator.FAIR_MARGIN * trade_finder.MARGIN_SLACK
        )


# --- what must not be suggested --------------------------------------------


def test_a_lopsided_value_trade_is_not_suggested(tf_league, db_session):
    """Charlie's RB3 would be a +170 lineup swing for me and a gain for them.

    It is still not a suggestion: I would be sending a 3000-value receiver for
    an 800-value back, and the finder does not propose robbery.
    """
    league, f = tf_league
    result = trade_finder.find_trades(db_session, league, limit=25, season=SEASON)

    # The deal really is lopsided, and really would help us both.
    analysed = evaluator.evaluate_trade(
        db_session,
        league,
        {"team_id": f["alpha"].id, "player_ids": [f["my_wr3"].id]},
        {"team_id": f["charlie"].id, "player_ids": [f["c_rb3"].id]},
        season=SEASON,
    )
    assert analysed["verdict"] != "fair"
    assert analysed["sides"]["a"]["lineup_delta"] > 0
    assert analysed["sides"]["b"]["lineup_delta"] > 0

    assert _find(result, ("My WR3",), ("C RB3",)) is None
    assert _for(result, "Charlie") == []


def test_a_trade_that_craters_the_opponent_is_not_suggested(tf_league, db_session):
    """Delta's RB1 is priced level with my WR3 and would be +180 for me.

    Delta would be left with one running back and an empty seat, so the offer
    is dead on arrival no matter how fair the price looks.
    """
    league, f = tf_league
    result = trade_finder.find_trades(db_session, league, limit=25, season=SEASON)

    analysed = evaluator.evaluate_trade(
        db_session,
        league,
        {"team_id": f["alpha"].id, "player_ids": [f["my_wr3"].id]},
        {"team_id": f["delta"].id, "player_ids": [f["d_rb1"].id]},
        season=SEASON,
    )
    # Fair on value, great for me -- and a disaster for them.
    assert analysed["verdict"] == "fair"
    assert analysed["sides"]["a"]["lineup_delta"] == 180.0
    assert analysed["sides"]["b"]["lineup_delta"] < trade_finder.MIN_OPP_DELTA

    assert _find(result, ("My WR3",), ("D RB1",)) is None
    assert _for(result, "Delta") == []


def test_kickers_and_defenses_are_never_suggested(tf_league, db_session):
    """Bravo's benched DEF2 clears every numeric filter and is still excluded."""
    league, f = tf_league
    result = trade_finder.find_trades(db_session, league, limit=25, season=SEASON)

    analysed = evaluator.evaluate_trade(
        db_session,
        league,
        {"team_id": f["alpha"].id, "player_ids": [f["my_def"].id]},
        {"team_id": f["bravo"].id, "player_ids": [f["b_def2"].id]},
        season=SEASON,
    )
    assert analysed["verdict"] == "fair"
    assert analysed["sides"]["a"]["lineup_delta"] == 85.0
    assert analysed["sides"]["b"]["lineup_delta"] == 0.0

    named = _names(result)
    assert named
    assert not {
        name for name in named if name.endswith(" K") or name.endswith("DEF")
    }
    assert {"My DEF", "B DEF2", "My K", "B K"}.isdisjoint(named)


# --- package shapes --------------------------------------------------------


def test_two_for_one_is_suggested_when_it_is_the_only_shape_that_fits(
    tf_league, db_session
):
    """No single player of mine is within 25% of Echo's 8000-value back.

    Pairing my WR2 and WR3 gets there (7800 against 8000), and it is the only
    Echo deal that survives.
    """
    league, _f = tf_league
    result = trade_finder.find_trades(db_session, league, limit=25, season=SEASON)

    echo = _for(result, "Echo")
    assert [row["kind"] for row in echo] == ["2for1"]
    deal = echo[0]
    assert tuple(p["full_name"] for p in deal["sends"]) == ("My WR2", "My WR3")
    assert tuple(p["full_name"] for p in deal["receives"]) == ("E RB1",)
    assert deal["my_lineup_delta"] == 30.0
    assert deal["opp_lineup_delta"] == 20.0
    assert deal["value_margin_pct"] == 0.025


def test_a_dominated_package_is_dropped(tf_league, db_session):
    """WR1+WR3 buys the same back for more points and less lineup gain."""
    league, f = tf_league
    result = trade_finder.find_trades(db_session, league, limit=25, season=SEASON)

    # On its own it would qualify: fair price, both lineups improve.
    analysed = evaluator.evaluate_trade(
        db_session,
        league,
        {
            "team_id": f["alpha"].id,
            "player_ids": [f["my_wr1"].id, f["my_wr3"].id],
        },
        {"team_id": f["echo"].id, "player_ids": [f["e_rb1"].id]},
        season=SEASON,
    )
    assert analysed["verdict"] == "fair"
    assert analysed["sides"]["a"]["lineup_delta"] == 20.0
    assert analysed["sides"]["b"]["lineup_delta"] == 30.0

    # ...but WR2+WR3 gets the same player back for 10 fewer ROS points and a
    # bigger lineup gain, so only that one is offered.
    assert _find(result, ("My WR1", "My WR3"), ("E RB1",)) is None
    assert _find(result, ("My WR2", "My WR3"), ("E RB1",)) is not None


def test_kinds_describe_the_package_shape(tf_league, db_session):
    league, _f = tf_league
    result = trade_finder.find_trades(db_session, league, limit=25, season=SEASON)
    for row in result["suggestions"]:
        assert row["kind"] == f"{len(row['sends'])}for{len(row['receives'])}"
        assert row["kind"] in {"1for1", "2for1", "1for2"}


# --- ranking, capping, determinism -----------------------------------------


def test_suggestions_are_ranked_by_my_gain(tf_league, db_session):
    league, _f = tf_league
    result = trade_finder.find_trades(db_session, league, limit=25, season=SEASON)
    deltas = [row["my_lineup_delta"] for row in result["suggestions"]]
    assert deltas == sorted(deltas, reverse=True)


def test_limit_truncates_the_ranked_list(tf_league, db_session):
    league, _f = tf_league
    full = trade_finder.find_trades(db_session, league, limit=25, season=SEASON)
    assert len(full["suggestions"]) > 3
    short = trade_finder.find_trades(db_session, league, limit=3, season=SEASON)
    assert short["suggestions"] == full["suggestions"][:3]


def test_no_opponent_floods_the_list(tf_league, db_session):
    league, _f = tf_league
    result = trade_finder.find_trades(db_session, league, limit=25, season=SEASON)
    for opponent in ("Bravo", "Charlie", "Delta", "Echo"):
        assert len(_for(result, opponent)) <= trade_finder.PER_OPPONENT_LIMIT


def test_results_are_deterministic(tf_league, db_session):
    league, _f = tf_league
    first = trade_finder.find_trades(db_session, league, limit=25, season=SEASON)
    second = trade_finder.find_trades(db_session, league, limit=25, season=SEASON)
    assert first == second
    assert _deals(first) == _deals(second)


# --- edges -----------------------------------------------------------------


def test_no_my_team_raises(tf_league, db_session):
    league, f = tf_league
    f["alpha"].is_my_team = False
    db_session.commit()

    with pytest.raises(ValueError, match="marked as yours"):
        trade_finder.find_trades(db_session, league, season=SEASON)


def test_a_league_with_no_rosters_suggests_nothing(db_session):
    league = make_league(db_session, roster_slots=TF_SLOTS, num_teams=2)
    make_team(db_session, league, "Alpha", is_my_team=True)
    make_team(db_session, league, "Bravo")
    db_session.commit()

    result = trade_finder.find_trades(db_session, league, season=SEASON)
    assert result["suggestions"] == []


def test_a_player_with_no_value_and_no_projection_is_never_offered(
    tf_league, db_session
):
    league, f = tf_league
    ghost = make_player(db_session, "Ghost WR", "WR", None)
    roster(db_session, league, f["alpha"], ghost)
    db_session.commit()

    result = trade_finder.find_trades(db_session, league, limit=25, season=SEASON)
    assert "Ghost WR" not in _names(result)


def test_sources_selection_flows_through(tf_league, db_session):
    """A source nothing was seeded under leaves every roster at zero points.

    Nothing can then improve a lineup, so nothing is suggested -- which is the
    cheap proof that ``sources`` reaches the projection lookup at all.
    """
    league, _f = tf_league
    result = trade_finder.find_trades(
        db_session, league, sources=("fantasypros",), limit=25, season=SEASON
    )
    assert result["suggestions"] == []


# --- performance -----------------------------------------------------------


def test_a_full_ten_team_league_is_fast(db_session):
    """10 teams x 16-man rosters, the shape the finder has to survive.

    Roughly 2,700 raw combinations per opponent before pruning. The budget is
    generous relative to the ~0.1s this actually takes so the test does not
    turn flaky on a loaded CI box, but it still catches a rewrite that starts
    querying inside the candidate loop.
    """
    import random

    rng = random.Random(11)
    slots = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1, "BN": 7}
    shape = ["QB"] * 2 + ["RB"] * 5 + ["WR"] * 5 + ["TE"] * 2 + ["K", "DEF"]

    league = make_league(
        db_session, roster_slots=slots, num_teams=10, league_key="manual.big"
    )
    for index in range(10):
        team = make_team(db_session, league, f"Team {index}", is_my_team=index == 0)
        for seat, position in enumerate(shape):
            points = round(rng.uniform(20, 320), 1)
            player = make_player(
                db_session, f"P{index}-{seat}", position, points
            )
            roster(db_session, league, team, player)
            set_value(db_session, player, round(points * rng.uniform(15, 25), 1))
    db_session.commit()

    start = time.perf_counter()
    result = trade_finder.find_trades(db_session, league, limit=10, season=SEASON)
    elapsed = time.perf_counter() - start

    assert len(result["suggestions"]) <= 10
    assert elapsed < 3.0, f"trade finder took {elapsed:.2f}s"


# --- route -----------------------------------------------------------------


def test_trade_finder_endpoint_returns_suggestions(client, tf_league):
    body = client.get("/api/leagues/manual.1/evaluate/trade-finder?limit=25").json()
    assert body["league_key"] == "manual.1"
    assert body["season"] == SEASON
    assert body["my_team"]["name"] == "Alpha"

    deal = _find(body, ("My WR3",), ("B RB3",))
    assert deal is not None
    assert deal["opponent"]["name"] == "Bravo"
    assert deal["opponent"]["team_id"] == tf_league[1]["bravo"].id
    assert deal["kind"] == "1for1"


def test_trade_finder_endpoint_honours_limit(client, tf_league):
    body = client.get("/api/leagues/manual.1/evaluate/trade-finder?limit=2").json()
    assert len(body["suggestions"]) == 2


def test_trade_finder_endpoint_rejects_a_bad_limit(client, tf_league):
    assert (
        client.get("/api/leagues/manual.1/evaluate/trade-finder?limit=0").status_code
        == 422
    )
    assert (
        client.get("/api/leagues/manual.1/evaluate/trade-finder?limit=26").status_code
        == 422
    )


def test_trade_finder_endpoint_validates_sources(client, tf_league):
    response = client.get(
        "/api/leagues/manual.1/evaluate/trade-finder?sources=nonsense"
    )
    assert response.status_code == 400
    assert "nonsense" in response.json()["detail"]


def test_trade_finder_endpoint_unknown_league_is_404(client, tf_league):
    response = client.get("/api/leagues/nope.1/evaluate/trade-finder")
    assert response.status_code == 404


def test_trade_finder_endpoint_without_my_team_is_409(client, tf_league, db_session):
    _league, f = tf_league
    f["alpha"].is_my_team = False
    db_session.commit()

    response = client.get("/api/leagues/manual.1/evaluate/trade-finder")
    assert response.status_code == 409
    assert "marked as yours" in response.json()["detail"]
