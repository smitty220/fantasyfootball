"""Manual league CRUD: leagues/teams/rosters not backed by Yahoo."""

from __future__ import annotations

import pytest

from app.models import League, LeaguePlayer, Player, Team
from app.services import scoring


@pytest.fixture()
def yahoo_league(db_session) -> League:
    league = League(
        league_key="461.l.999",
        game_key="461",
        name="Real Yahoo League",
        season=2026,
        source="yahoo",
    )
    db_session.add(league)
    db_session.commit()
    db_session.refresh(league)
    return league


def _create_manual_league(client, **overrides):
    body = {"name": "Friends League", "season": 2026}
    body.update(overrides)
    return client.post("/api/manual/leagues", json=body)


# --- league CRUD -----------------------------------------------------------


def test_create_league_defaults(client):
    response = _create_manual_league(client)
    assert response.status_code == 201
    body = response.json()
    assert body["league_key"] == "manual.1"
    assert body["name"] == "Friends League"
    assert body["season"] == 2026
    assert body["is_keeper"] is False
    assert body["num_teams"] == 12
    assert body["roster_slots"] == scoring.DEFAULT_ROSTER_SLOTS
    assert body["scoring_rules"] == scoring.get_preset("half_ppr")


def test_create_league_custom_scoring_rules(client, db_session):
    custom_rules = {"per_stat": {"pass_td": 6}}
    response = _create_manual_league(client, scoring_rules=custom_rules)
    assert response.status_code == 201
    assert response.json()["scoring_rules"] == custom_rules

    league = db_session.query(League).one()
    assert league.scoring_type == "custom"
    assert league.source == "manual"


def test_create_league_unknown_preset_400(client):
    response = _create_manual_league(client, scoring_preset="nonsense")
    assert response.status_code == 400


def test_update_league_partial(client):
    league_key = _create_manual_league(client).json()["league_key"]

    response = client.put(
        f"/api/manual/leagues/{league_key}",
        json={"name": "Renamed League", "num_teams": 10},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Renamed League"
    assert body["num_teams"] == 10
    # untouched fields survive the partial update
    assert body["is_keeper"] is False
    assert body["roster_slots"] == scoring.DEFAULT_ROSTER_SLOTS


def test_update_league_scoring_rules_marks_custom(client, db_session):
    league_key = _create_manual_league(client).json()["league_key"]
    new_rules = {"per_stat": {"rec": 1.0}}

    response = client.put(
        f"/api/manual/leagues/{league_key}", json={"scoring_rules": new_rules}
    )
    assert response.status_code == 200
    assert response.json()["scoring_rules"] == new_rules

    league = db_session.query(League).filter(League.league_key == league_key).one()
    assert league.scoring_type == "custom"


def test_update_unknown_league_404(client):
    assert client.put("/api/manual/leagues/manual.999", json={"name": "x"}).status_code == 404


def test_update_yahoo_league_409(client, yahoo_league):
    response = client.put(
        f"/api/manual/leagues/{yahoo_league.league_key}", json={"name": "x"}
    )
    assert response.status_code == 409


def test_delete_league_cascades(client, db_session):
    league_key = _create_manual_league(client).json()["league_key"]
    team_id = client.post(
        f"/api/manual/leagues/{league_key}/teams", json={"name": "My Team"}
    ).json()["id"]

    player = Player(full_name="Some Player", position="RB")
    db_session.add(player)
    db_session.commit()
    client.post(f"/api/manual/teams/{team_id}/roster", json={"player_id": player.id})

    response = client.delete(f"/api/manual/leagues/{league_key}")
    assert response.status_code == 204

    league = db_session.query(League).filter(League.league_key == league_key).one_or_none()
    assert league is None
    assert db_session.query(Team).filter(Team.id == team_id).one_or_none() is None
    assert db_session.query(LeaguePlayer).count() == 0


def test_delete_yahoo_league_409(client, yahoo_league):
    assert client.delete(f"/api/manual/leagues/{yahoo_league.league_key}").status_code == 409


def test_delete_unknown_league_404(client):
    assert client.delete("/api/manual/leagues/manual.999").status_code == 404


# --- team CRUD ---------------------------------------------------------


def test_create_team(client):
    league_key = _create_manual_league(client).json()["league_key"]
    response = client.post(
        f"/api/manual/leagues/{league_key}/teams",
        json={"name": "My Team", "manager_name": "Ryan"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["team_key"] == f"manual.1.t.{body['id']}"
    assert body["manager_name"] == "Ryan"
    assert body["is_my_team"] is False


def test_create_team_on_yahoo_league_409(client, yahoo_league):
    response = client.post(
        f"/api/manual/leagues/{yahoo_league.league_key}/teams", json={"name": "x"}
    )
    assert response.status_code == 409


def test_is_my_team_exclusivity_on_create(client):
    league_key = _create_manual_league(client).json()["league_key"]
    t1 = client.post(
        f"/api/manual/leagues/{league_key}/teams",
        json={"name": "Team One", "is_my_team": True},
    ).json()
    t2 = client.post(
        f"/api/manual/leagues/{league_key}/teams",
        json={"name": "Team Two", "is_my_team": True},
    ).json()

    assert t2["is_my_team"] is True
    refreshed_t1 = client.put(f"/api/manual/teams/{t1['id']}", json={}).json()
    assert refreshed_t1["is_my_team"] is False


def test_is_my_team_exclusivity_on_update(client):
    league_key = _create_manual_league(client).json()["league_key"]
    t1 = client.post(
        f"/api/manual/leagues/{league_key}/teams",
        json={"name": "Team One", "is_my_team": True},
    ).json()
    t2 = client.post(
        f"/api/manual/leagues/{league_key}/teams", json={"name": "Team Two"}
    ).json()

    client.put(f"/api/manual/teams/{t2['id']}", json={"is_my_team": True})

    refreshed_t1 = client.put(f"/api/manual/teams/{t1['id']}", json={}).json()
    assert refreshed_t1["is_my_team"] is False


def test_update_team_on_yahoo_league_409(client, db_session, yahoo_league):
    team = Team(team_key="461.l.999.t.1", league_id=yahoo_league.id, name="Rivals")
    db_session.add(team)
    db_session.commit()

    response = client.put(f"/api/manual/teams/{team.id}", json={"name": "x"})
    assert response.status_code == 409


def test_delete_team_frees_players(client, db_session):
    league_key = _create_manual_league(client).json()["league_key"]
    team_id = client.post(
        f"/api/manual/leagues/{league_key}/teams", json={"name": "My Team"}
    ).json()["id"]

    player = Player(full_name="Some Player", position="RB")
    db_session.add(player)
    db_session.commit()
    client.post(f"/api/manual/teams/{team_id}/roster", json={"player_id": player.id})

    response = client.delete(f"/api/manual/teams/{team_id}")
    assert response.status_code == 204

    assert db_session.query(Team).filter(Team.id == team_id).one_or_none() is None
    assert db_session.query(LeaguePlayer).filter(
        LeaguePlayer.player_id == player.id
    ).one_or_none() is None


def test_update_unknown_team_404(client):
    assert client.put("/api/manual/teams/999", json={"name": "x"}).status_code == 404


# --- roster ----------------------------------------------------------------


def test_add_and_list_roster(client, db_session):
    league_key = _create_manual_league(client).json()["league_key"]
    team_id = client.post(
        f"/api/manual/leagues/{league_key}/teams", json={"name": "My Team"}
    ).json()["id"]

    player = Player(full_name="Some Player", position="RB", nfl_team="KC")
    db_session.add(player)
    db_session.commit()

    add_response = client.post(
        f"/api/manual/teams/{team_id}/roster", json={"player_id": player.id}
    )
    assert add_response.status_code == 201
    assert add_response.json()["full_name"] == "Some Player"

    roster = client.get(f"/api/manual/teams/{team_id}/roster").json()
    assert len(roster) == 1
    assert roster[0]["player_id"] == player.id
    assert roster[0]["nfl_team"] == "KC"


def test_add_roster_player_already_on_a_team_409(client, db_session):
    league_key = _create_manual_league(client).json()["league_key"]
    team1 = client.post(
        f"/api/manual/leagues/{league_key}/teams", json={"name": "Team One"}
    ).json()["id"]
    team2 = client.post(
        f"/api/manual/leagues/{league_key}/teams", json={"name": "Team Two"}
    ).json()["id"]

    player = Player(full_name="Some Player", position="RB")
    db_session.add(player)
    db_session.commit()

    client.post(f"/api/manual/teams/{team1}/roster", json={"player_id": player.id})
    dup_response = client.post(
        f"/api/manual/teams/{team2}/roster", json={"player_id": player.id}
    )
    assert dup_response.status_code == 409


def test_add_roster_unknown_player_404(client):
    league_key = _create_manual_league(client).json()["league_key"]
    team_id = client.post(
        f"/api/manual/leagues/{league_key}/teams", json={"name": "My Team"}
    ).json()["id"]

    response = client.post(f"/api/manual/teams/{team_id}/roster", json={"player_id": 999})
    assert response.status_code == 404


def test_add_roster_on_yahoo_league_team_409(client, db_session, yahoo_league):
    team = Team(team_key="461.l.999.t.1", league_id=yahoo_league.id, name="Rivals")
    player = Player(full_name="Some Player", position="RB")
    db_session.add_all([team, player])
    db_session.commit()

    response = client.post(
        f"/api/manual/teams/{team.id}/roster", json={"player_id": player.id}
    )
    assert response.status_code == 409


def test_remove_roster_player(client, db_session):
    league_key = _create_manual_league(client).json()["league_key"]
    team_id = client.post(
        f"/api/manual/leagues/{league_key}/teams", json={"name": "My Team"}
    ).json()["id"]

    player = Player(full_name="Some Player", position="RB")
    db_session.add(player)
    db_session.commit()

    client.post(f"/api/manual/teams/{team_id}/roster", json={"player_id": player.id})
    remove_response = client.delete(f"/api/manual/teams/{team_id}/roster/{player.id}")
    assert remove_response.status_code == 204

    assert client.get(f"/api/manual/teams/{team_id}/roster").json() == []


def test_remove_roster_player_not_present_404(client):
    league_key = _create_manual_league(client).json()["league_key"]
    team_id = client.post(
        f"/api/manual/leagues/{league_key}/teams", json={"name": "My Team"}
    ).json()["id"]

    assert client.delete(f"/api/manual/teams/{team_id}/roster/999").status_code == 404
