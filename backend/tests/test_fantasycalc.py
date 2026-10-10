"""FantasyCalc service tests. All HTTP is mocked via respx."""

from __future__ import annotations

from datetime import datetime

import httpx
import respx

from app.models import League, Player, SyncLog, TradeValue
from app.services import fantasycalc


def _entry(name, sleeper_id, position, value, trend=0, extra=None):
    player = {"id": 1, "name": name, "sleeperId": sleeper_id, "position": position}
    if extra:
        player.update(extra)
    return {"player": player, "value": value, "trend30Day": trend}


@respx.mock
def test_refresh_trade_values_stores_both_formats(db_session):
    gibbs = Player(full_name="Jahmyr Gibbs", sleeper_id="9221", position="RB")
    db_session.add(gibbs)
    db_session.commit()

    redraft_route = respx.get(
        fantasycalc.VALUES_URL, params={"isDynasty": "false"}
    ).mock(
        return_value=httpx.Response(
            200, json=[_entry("Jahmyr Gibbs", "9221", "RB", 10161, trend=80)]
        )
    )
    dynasty_route = respx.get(
        fantasycalc.VALUES_URL, params={"isDynasty": "true"}
    ).mock(
        return_value=httpx.Response(
            200,
            json=[
                _entry("Jahmyr Gibbs", "9221", "RB", 11488, trend=730),
                # Dynasty-only synthetic draft-pick row; must be skipped.
                _entry("2027 1st (Early)", "FP_2027_early_0", "PICK", 4472),
            ],
        )
    )

    message = fantasycalc.refresh_trade_values(db_session)

    assert redraft_route.called
    assert dynasty_route.called

    rows = {r.format: r for r in db_session.query(TradeValue).filter(
        TradeValue.player_id == gibbs.id
    )}
    redraft = fantasycalc.DEFAULT_PROFILE.label(False)
    dynasty = fantasycalc.DEFAULT_PROFILE.label(True)
    assert (redraft, dynasty) == ("redraft:12:1:1", "dynasty:12:1:1")
    assert set(rows) == {redraft, dynasty}
    assert rows[redraft].value == 10161
    assert rows[redraft].trend_30d == 80
    assert rows[redraft].source == "fantasycalc"
    assert rows[dynasty].value == 11488
    assert rows[dynasty].trend_30d == 730

    # The PICK row never became a Player or a TradeValue.
    assert db_session.query(Player).count() == 1
    assert db_session.query(TradeValue).count() == 2

    log = db_session.query(SyncLog).filter(SyncLog.resource == "fantasycalc").one()
    assert log.status == "success"
    assert "redraft" in message and "dynasty" in message


@respx.mock
def test_refresh_trade_values_matches_by_name_when_no_sleeper_id_hit(db_session):
    player = Player(full_name="Amon-Ra St. Brown", position="WR")
    db_session.add(player)
    db_session.commit()

    respx.get(fantasycalc.VALUES_URL, params={"isDynasty": "false"}).mock(
        return_value=httpx.Response(
            200,
            json=[_entry("Amon-Ra St. Brown", "unknown-sleeper-id", "WR", 9000)],
        )
    )
    respx.get(fantasycalc.VALUES_URL, params={"isDynasty": "true"}).mock(
        return_value=httpx.Response(200, json=[])
    )

    fantasycalc.refresh_trade_values(db_session)

    row = db_session.query(TradeValue).filter(TradeValue.player_id == player.id).one()
    assert row.value == 9000


@respx.mock
def test_refresh_trade_values_counts_unmatched_without_creating_players(db_session):
    respx.get(fantasycalc.VALUES_URL, params={"isDynasty": "false"}).mock(
        return_value=httpx.Response(
            200, json=[_entry("Totally Unknown Guy", "no-match", "WR", 500)]
        )
    )
    respx.get(fantasycalc.VALUES_URL, params={"isDynasty": "true"}).mock(
        return_value=httpx.Response(200, json=[])
    )

    message = fantasycalc.refresh_trade_values(db_session)

    assert db_session.query(Player).count() == 0
    assert db_session.query(TradeValue).count() == 0
    assert "1 unmatched" in message


def _league(key, *, num_teams=10, slots=None, rec=1.0):
    return League(
        league_key=key,
        game_key="nfl",
        name=key,
        season=2026,
        num_teams=num_teams,
        source="manual",
        settings_json={
            "roster_slots": slots if slots is not None else {"QB": 1, "RB": 2, "BN": 6},
            "scoring_rules": {"per_stat": {"rec": rec}},
        },
    )


def test_profile_superflex_is_two_qbs():
    for slot in ("Q/W/R/T", "OP", "SUPERFLEX"):
        league = _league("sf", slots={"QB": 1, "RB": 2, slot: 1, "BN": 6})
        assert fantasycalc.league_profile(league) == (10, 2, 1.0)
    assert fantasycalc.trade_format(
        _league("sf", slots={"QB": 1, "Q/W/R/T": 1}), dynasty=True
    ) == "dynasty:10:2:1"


def test_profile_two_qb_slots_is_two_qbs():
    assert fantasycalc.league_profile(_league("2qb", slots={"QB": 2})).num_qbs == 2


def test_profile_one_qb_and_flex_is_one_qb():
    league = _league("std", slots={"QB": 1, "W/R/T": 1, "BN": 6})
    assert fantasycalc.league_profile(league).num_qbs == 1


def test_profile_rounds_ppr_to_nearest_supported_step():
    for rec, expected in ((0, 0.0), (0.25, 0.0), (0.4, 0.5), (0.5, 0.5), (0.8, 1.0), (1, 1.0)):
        assert fantasycalc.league_profile(_league("p", rec=rec)).ppr == expected
    assert fantasycalc.trade_format(_league("p", rec=0.5)) == "redraft:10:1:0.5"


def test_profile_uses_team_count_and_defaults():
    assert fantasycalc.league_profile(_league("t", num_teams=14)).num_teams == 14
    assert fantasycalc.league_profile(_league("t", num_teams=0)).num_teams == 12
    assert fantasycalc.league_profile(None) == fantasycalc.DEFAULT_PROFILE
    # Longest possible label still fits the String(16) column.
    assert len(fantasycalc.Profile(12, 2, 0.5).label(False)) <= 16


@respx.mock
def test_refresh_fetches_once_per_distinct_profile(db_session):
    db_session.add(Player(full_name="Josh Allen", sleeper_id="4984", position="QB"))
    db_session.add_all(
        [
            _league("keeper"),
            _league("sf", slots={"QB": 1, "Q/W/R/T": 1}),
            _league("keeper-twin"),  # same profile as "keeper"
        ]
    )
    db_session.commit()

    def respond(request):
        qbs = request.url.params["numQbs"]
        return httpx.Response(
            200, json=[_entry("Josh Allen", "4984", "QB", 3000 * int(qbs))]
        )

    route = respx.get(fantasycalc.VALUES_URL).mock(side_effect=respond)

    fantasycalc.refresh_trade_values(db_session)

    sent = sorted(
        (r.request.url.params["isDynasty"], r.request.url.params["numQbs"],
         r.request.url.params["numTeams"], r.request.url.params["ppr"])
        for r in route.calls
    )
    assert sent == [
        ("false", "1", "10", "1.0"),
        ("false", "2", "10", "1.0"),
        ("true", "1", "10", "1.0"),
        ("true", "2", "10", "1.0"),
    ]
    values = {r.format: r.value for r in db_session.query(TradeValue)}
    assert values == {
        "redraft:10:1:1": 3000,
        "dynasty:10:1:1": 3000,
        "redraft:10:2:1": 6000,
        "dynasty:10:2:1": 6000,
    }


@respx.mock
def test_refresh_removes_rows_for_profiles_no_league_uses(db_session):
    allen = Player(full_name="Josh Allen", sleeper_id="4984", position="QB")
    db_session.add_all([allen, _league("only")])
    db_session.commit()
    for label in ("redraft", "dynasty", "redraft:12:1:1", "redraft:10:2:1"):
        db_session.add(
            TradeValue(player_id=allen.id, source="fantasycalc", format=label,
                       value=1, fetched_at=datetime(2026, 1, 1))
        )
    db_session.commit()

    respx.get(fantasycalc.VALUES_URL).mock(
        return_value=httpx.Response(200, json=[_entry("Josh Allen", "4984", "QB", 3000)])
    )
    fantasycalc.refresh_trade_values(db_session)

    assert {r.format for r in db_session.query(TradeValue)} == {
        "redraft:10:1:1",
        "dynasty:10:1:1",
    }
