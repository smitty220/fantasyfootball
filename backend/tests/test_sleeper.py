"""Sleeper service tests. All HTTP is mocked via respx; nothing leaves the machine."""

from __future__ import annotations

import httpx
import respx

from app.models import Player, SyncLog, TrendingSignal
from app.services import sleeper

PLAYERS_URL = f"{sleeper.BASE_URL}/players/nfl"


def _players_payload(**overrides) -> dict:
    payload = {
        "4046": {
            "player_id": "4046",
            "full_name": "Patrick Mahomes",
            "position": "QB",
            "team": "KC",
            "injury_status": "Questionable",
            "yahoo_id": 30123,
            "espn_id": 3139477,
            "gsis_id": "00-0033873",
        },
        "HOU": {
            "player_id": "HOU",
            "first_name": "Houston",
            "last_name": "Texans",
            "position": "DEF",
            "team": "HOU",
            "injury_status": None,
        },
        "99999": {
            "player_id": "99999",
            "full_name": "Some Linebacker",
            "position": "LB",
            "team": "SF",
        },
    }
    payload.update(overrides)
    return payload


@respx.mock
def test_refresh_players_creates_and_matches(db_session):
    respx.get(PLAYERS_URL).mock(
        return_value=httpx.Response(200, json=_players_payload())
    )

    message = sleeper.refresh_players(db_session)

    players = {p.full_name: p for p in db_session.query(Player).all()}
    assert set(players) == {"Patrick Mahomes", "Houston Texans"}  # LB filtered out

    mahomes = players["Patrick Mahomes"]
    assert mahomes.sleeper_id == "4046"
    assert mahomes.position == "QB"
    assert mahomes.nfl_team == "KC"
    assert mahomes.injury_status == "Questionable"
    assert mahomes.yahoo_id == "30123"
    assert mahomes.espn_id == "3139477"
    assert mahomes.gsis_id == "00-0033873"

    texans = players["Houston Texans"]
    assert texans.sleeper_id == "HOU"
    assert texans.position == "DEF"

    assert "2 created" in message
    log = db_session.query(SyncLog).filter(SyncLog.resource == "sleeper_players").one()
    assert log.status == "success"


@respx.mock
def test_refresh_players_enriches_existing_and_updates_injury(db_session):
    existing = Player(full_name="Pat Mahomes", yahoo_id="30123", position="QB")
    db_session.add(existing)
    db_session.commit()

    respx.get(PLAYERS_URL).mock(
        return_value=httpx.Response(200, json=_players_payload())
    )

    sleeper.refresh_players(db_session)

    db_session.refresh(existing)
    assert existing.sleeper_id == "4046"  # matched via yahoo_id, then filled
    assert existing.full_name == "Patrick Mahomes"  # refreshed
    assert existing.injury_status == "Questionable"
    assert db_session.query(Player).filter(Player.full_name == "Patrick Mahomes").count() == 1


@respx.mock
def test_refresh_players_never_overwrites_conflicting_id(db_session):
    existing = Player(
        full_name="Pat Mahomes",
        sleeper_id="4046",
        yahoo_id="DIFFERENT-ID",
        position="QB",
    )
    db_session.add(existing)
    db_session.commit()

    respx.get(PLAYERS_URL).mock(
        return_value=httpx.Response(200, json=_players_payload())
    )

    sleeper.refresh_players(db_session)

    db_session.refresh(existing)
    assert existing.yahoo_id == "DIFFERENT-ID"  # never overwritten
    assert existing.injury_status == "Questionable"  # still refreshed


@respx.mock
def test_refresh_trending_upserts_and_dedupes(db_session):
    alpha = Player(full_name="Alpha", sleeper_id="1", position="RB")
    bravo = Player(full_name="Bravo", sleeper_id="2", position="WR")
    db_session.add_all([alpha, bravo])
    db_session.commit()

    add_url = f"{sleeper.BASE_URL}/players/nfl/trending/add"
    drop_url = f"{sleeper.BASE_URL}/players/nfl/trending/drop"

    respx.get(add_url).mock(
        return_value=httpx.Response(
            200, json=[{"count": 100, "player_id": "1"}, {"count": 50, "player_id": "2"}]
        )
    )
    respx.get(drop_url).mock(return_value=httpx.Response(200, json=[]))

    sleeper.refresh_trending(db_session)

    rows = db_session.query(TrendingSignal).all()
    assert len(rows) == 2
    counts = {(r.player_id, r.kind): r.count for r in rows}
    assert counts[(alpha.id, "add")] == 100
    assert counts[(bravo.id, "add")] == 50

    # Second run: alpha drops out of trending/add, bravo's count changes, and
    # a genuinely new add/drop mix must not create duplicate rows.
    respx.routes.clear()
    respx.get(add_url).mock(
        return_value=httpx.Response(200, json=[{"count": 75, "player_id": "2"}])
    )
    respx.get(drop_url).mock(
        return_value=httpx.Response(200, json=[{"count": 10, "player_id": "1"}])
    )

    sleeper.refresh_trending(db_session)

    rows = db_session.query(TrendingSignal).all()
    assert len(rows) == 2  # no duplicates
    by_key = {(r.player_id, r.kind): r.count for r in rows}
    assert by_key == {(bravo.id, "add"): 75, (alpha.id, "drop"): 10}
    # The stale alpha/add row from the first run is gone.
    assert (alpha.id, "add") not in by_key


@respx.mock
def test_refresh_trending_skips_unmatched_players(db_session):
    add_url = f"{sleeper.BASE_URL}/players/nfl/trending/add"
    drop_url = f"{sleeper.BASE_URL}/players/nfl/trending/drop"
    respx.get(add_url).mock(
        return_value=httpx.Response(200, json=[{"count": 5, "player_id": "unknown"}])
    )
    respx.get(drop_url).mock(return_value=httpx.Response(200, json=[]))

    message = sleeper.refresh_trending(db_session)

    assert db_session.query(TrendingSignal).count() == 0
    assert "1 unmatched" in message
