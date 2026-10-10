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
def test_refresh_players_only_enriches_existing_players(db_session):
    # Yahoo created these; Sleeper fills IDs and gaps but never adds players.
    mahomes = Player(full_name="Patrick Mahomes", yahoo_id="30123", position="QB",
                     nfl_team="KC", injury_status="Q")
    texans = Player(full_name="Texans", yahoo_id="100034", position="DEF", nfl_team="HOU")
    db_session.add_all([mahomes, texans])
    db_session.commit()
    respx.get(PLAYERS_URL).mock(
        return_value=httpx.Response(200, json=_players_payload())
    )

    message = sleeper.refresh_players(db_session)

    assert db_session.query(Player).count() == 2
    db_session.refresh(mahomes)
    db_session.refresh(texans)
    assert mahomes.sleeper_id == "4046"  # matched via yahoo_id
    assert (mahomes.espn_id, mahomes.gsis_id) == ("3139477", "00-0033873")
    assert texans.sleeper_id == "HOU"  # matched via position + team
    assert "2 matched" in message
    log = db_session.query(SyncLog).filter(SyncLog.resource == "sleeper_players").one()
    assert log.status == "success"


@respx.mock
def test_refresh_players_leaves_yahoo_fields_alone(db_session):
    existing = Player(full_name="Pat Mahomes", yahoo_id="30123", position="QB",
                      nfl_team="KC", injury_status="Q")
    db_session.add(existing)
    db_session.commit()
    respx.get(PLAYERS_URL).mock(
        return_value=httpx.Response(200, json=_players_payload())
    )

    sleeper.refresh_players(db_session)

    db_session.refresh(existing)
    assert existing.full_name == "Pat Mahomes"
    assert existing.injury_status == "Q"  # Yahoo's, not Sleeper's "Questionable"


@respx.mock
def test_refresh_players_name_match_fills_a_yahoo_only_rookie(db_session):
    rookie = Player(full_name="Patrick Mahomes II", position="QB", yahoo_id="99")
    db_session.add(rookie)
    db_session.commit()
    payload = _players_payload()
    payload["4046"]["yahoo_id"] = None  # Sleeper doesn't know his Yahoo ID yet
    respx.get(PLAYERS_URL).mock(return_value=httpx.Response(200, json=payload))

    sleeper.refresh_players(db_session)

    db_session.refresh(rookie)
    assert rookie.sleeper_id == "4046"


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
    assert existing.espn_id == "3139477"  # missing IDs still filled


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
