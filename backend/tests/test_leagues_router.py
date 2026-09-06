"""League router tests against a seeded DB (no Yahoo calls)."""

from __future__ import annotations

import pytest

from datetime import datetime, timezone

from app.models import League, LeaguePlayer, Matchup, Player, Team, TradeValue
from app.services.yahoo import oauth, sync


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture()
def seeded(db_session) -> League:
    league = League(
        league_key="461.l.12345",
        game_key="461",
        name="Test League",
        season=2026,
        num_teams=2,
        scoring_type="head",
        current_week=3,
    )
    db_session.add(league)
    db_session.flush()

    home = Team(
        team_key="461.l.12345.t.1",
        league_id=league.id,
        name="My Squad",
        manager_name="Ryan",
        is_my_team=True,
        rank=1,
        wins=2,
        losses=0,
        points_for=210.5,
    )
    away = Team(
        team_key="461.l.12345.t.2",
        league_id=league.id,
        name="Rivals",
        manager_name="Someone",
        rank=2,
        wins=0,
        losses=2,
        points_for=150.0,
    )
    db_session.add_all([home, away])
    db_session.flush()

    db_session.add(
        Matchup(
            league_id=league.id,
            week=3,
            home_team_id=home.id,
            away_team_id=away.id,
            home_points=100.0,
            away_points=90.0,
            status="midevent",
        )
    )
    db_session.add(
        Matchup(
            league_id=league.id,
            week=2,
            home_team_id=home.id,
            away_team_id=away.id,
            home_points=110.0,
            away_points=95.0,
            status="postevent",
        )
    )

    wr = Player(full_name="Free Agent WR", position="WR", nfl_team="SF", yahoo_id="1")
    rb = Player(full_name="Waiver RB", position="RB", nfl_team="KC", yahoo_id="2")
    taken = Player(full_name="Rostered TE", position="TE", nfl_team="BUF", yahoo_id="3")
    db_session.add_all([wr, rb, taken])
    db_session.flush()

    db_session.add_all(
        [
            LeaguePlayer(
                league_id=league.id, player_id=wr.id, status="FA", percent_owned=42.0
            ),
            LeaguePlayer(
                league_id=league.id, player_id=rb.id, status="W", percent_owned=8.0
            ),
            LeaguePlayer(
                league_id=league.id,
                player_id=taken.id,
                status="T",
                on_team_id=home.id,
                percent_owned=99.0,
            ),
        ]
    )
    db_session.commit()
    db_session.refresh(league)
    return league


def test_list_leagues(client, seeded):
    body = client.get("/api/leagues").json()
    assert len(body) == 1
    assert body[0]["league_key"] == "461.l.12345"
    assert body[0]["is_keeper"] is False
    assert body[0]["current_week"] == 3


def test_list_leagues_empty(client):
    assert client.get("/api/leagues").json() == []


def test_league_teams(client, seeded):
    body = client.get("/api/leagues/461.l.12345/teams").json()
    assert [t["name"] for t in body] == ["My Squad", "Rivals"]
    assert body[0]["is_my_team"] is True
    assert body[0]["points_for"] == 210.5


def test_league_teams_unknown_league(client):
    assert client.get("/api/leagues/461.l.99999/teams").status_code == 404


def test_free_agents_excludes_rostered(client, seeded):
    body = client.get("/api/leagues/461.l.12345/free-agents").json()
    assert [p["full_name"] for p in body] == ["Free Agent WR", "Waiver RB"]
    assert body[0]["status"] == "FA"
    assert body[1]["status"] == "W"


def test_free_agents_position_filter(client, seeded):
    body = client.get("/api/leagues/461.l.12345/free-agents?position=rb").json()
    assert [p["full_name"] for p in body] == ["Waiver RB"]


def test_matchups_defaults_to_current_week(client, seeded):
    body = client.get("/api/leagues/461.l.12345/matchups").json()
    assert len(body) == 1
    assert body[0]["week"] == 3


def test_matchups_explicit_week(client, seeded):
    body = client.get("/api/leagues/461.l.12345/matchups?week=2").json()
    assert len(body) == 1
    assert body[0]["home_points"] == 110.0


def test_discover_requires_yahoo_connection(client):
    response = client.post("/api/leagues/discover")
    assert response.status_code == 409


def test_sync_requires_yahoo_connection(client, seeded):
    response = client.post("/api/leagues/461.l.12345/sync")
    assert response.status_code == 409


def test_discover_surfaces_yahoo_failure(client, db_session, monkeypatch):
    def boom(db):
        raise RuntimeError("yahoo exploded")

    monkeypatch.setattr(sync, "discover_leagues", boom)
    assert client.post("/api/leagues/discover").status_code == 502


def test_sync_returns_result(client, seeded, monkeypatch):
    monkeypatch.setattr(
        sync,
        "sync_league",
        lambda db, league_key: {
            "league_key": league_key,
            "league_id": seeded.id,
            "week": 3,
            "status": "partial",
            "errors": ["yahoo.draft: nope"],
        },
    )

    body = client.post("/api/leagues/461.l.12345/sync").json()
    assert body["status"] == "partial"
    assert body["errors"] == ["yahoo.draft: nope"]


def test_discover_returns_leagues(client, db_session, monkeypatch, seeded):
    monkeypatch.setattr(sync, "discover_leagues", lambda db: [seeded])

    body = client.post("/api/leagues/discover").json()
    assert body["discovered"] == 1
    assert body["leagues"][0]["league_key"] == "461.l.12345"


def test_not_connected_error_is_a_yahoo_auth_error():
    assert issubclass(oauth.YahooNotConnectedError, oauth.YahooAuthError)


@pytest.fixture()
def manual_seeded(db_session) -> League:
    league = League(
        league_key="manual.1",
        game_key="manual",
        name="Manual League",
        season=2026,
        num_teams=1,
        source="manual",
    )
    db_session.add(league)
    db_session.flush()

    team = Team(team_key="manual.1.t.1", league_id=league.id, name="My Team", is_my_team=True)
    db_session.add(team)
    db_session.flush()

    high_value_fa = Player(full_name="Undrafted WR", position="WR", nfl_team="SF")
    low_value_fa = Player(full_name="Deep Bench RB", position="RB", nfl_team="KC")
    no_value_fa = Player(full_name="Waiver TE", position="TE", nfl_team="BUF")
    rostered = Player(full_name="My Starter", position="QB", nfl_team="DAL")
    non_fantasy_position = Player(full_name="Random DL", position="DL", nfl_team="NYJ")
    db_session.add_all(
        [high_value_fa, low_value_fa, no_value_fa, rostered, non_fantasy_position]
    )
    db_session.flush()

    db_session.add(
        LeaguePlayer(
            league_id=league.id, player_id=rostered.id, status="T", on_team_id=team.id
        )
    )
    db_session.add_all(
        [
            TradeValue(
                player_id=high_value_fa.id,
                source="fantasycalc",
                format="redraft",
                value=5000,
                fetched_at=_utcnow(),
            ),
            TradeValue(
                player_id=low_value_fa.id,
                source="fantasycalc",
                format="redraft",
                value=1000,
                fetched_at=_utcnow(),
            ),
        ]
    )
    db_session.commit()
    db_session.refresh(league)
    return league


def test_manual_free_agents_excludes_rostered_and_non_fantasy_positions(
    client, manual_seeded
):
    body = client.get("/api/leagues/manual.1/free-agents").json()
    names = [p["full_name"] for p in body]
    assert names == ["Undrafted WR", "Deep Bench RB", "Waiver TE"]
    assert all(p["status"] == "FA" for p in body)


def test_manual_free_agents_ordered_by_trade_value_desc_nulls_last(
    client, manual_seeded
):
    body = client.get("/api/leagues/manual.1/free-agents").json()
    assert [p["full_name"] for p in body] == [
        "Undrafted WR",
        "Deep Bench RB",
        "Waiver TE",
    ]


def test_manual_free_agents_position_filter(client, manual_seeded):
    body = client.get("/api/leagues/manual.1/free-agents?position=rb").json()
    assert [p["full_name"] for p in body] == ["Deep Bench RB"]
