"""FantasyCalc service tests. All HTTP is mocked via respx."""

from __future__ import annotations

import httpx
import respx

from app.models import Player, SyncLog, TradeValue
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
    assert set(rows) == {"redraft", "dynasty"}
    assert rows["redraft"].value == 10161
    assert rows["redraft"].trend_30d == 80
    assert rows["redraft"].source == "fantasycalc"
    assert rows["dynasty"].value == 11488
    assert rows["dynasty"].trend_30d == 730

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
