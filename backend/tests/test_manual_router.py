"""Manual league CRUD: leagues/teams/rosters not backed by Yahoo."""

from __future__ import annotations

import pytest

from app.models import League, LeaguePlayer, Player, RosterSlot, Team
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


# --- lineup ----------------------------------------------------------------


LINEUP_SLOTS = {"QB": 1, "RB": 2, "WR": 1, "FLEX": 1, "BN": 4}


@pytest.fixture()
def lineup_team(client, db_session):
    """A manual team with one player at each of QB/RB/RB/WR/TE, plus a rival."""
    league_key = _create_manual_league(client, roster_slots=LINEUP_SLOTS).json()[
        "league_key"
    ]
    team_id = client.post(
        f"/api/manual/leagues/{league_key}/teams", json={"name": "My Team"}
    ).json()["id"]
    rival_id = client.post(
        f"/api/manual/leagues/{league_key}/teams", json={"name": "Rivals"}
    ).json()["id"]

    players = {
        name: Player(full_name=name, position=position)
        for name, position in (
            ("Quinn QB", "QB"),
            ("Rob RB", "RB"),
            ("Rex RB", "RB"),
            ("Wade WR", "WR"),
            ("Tom TE", "TE"),
            ("Rival RB", "RB"),
        )
    }
    db_session.add_all(players.values())
    db_session.commit()

    for name, player in players.items():
        target = rival_id if name == "Rival RB" else team_id
        client.post(f"/api/manual/teams/{target}/roster", json={"player_id": player.id})

    return {
        "league_key": league_key,
        "team_id": team_id,
        "rival_id": rival_id,
        "players": players,
    }


def _put_lineup(client, team_id: int, assignments: list[dict]):
    return client.put(
        f"/api/manual/teams/{team_id}/lineup", json={"assignments": assignments}
    )


def _seats(body: dict) -> list[tuple[str, str | None]]:
    return [
        (entry["slot"], entry["player"]["full_name"] if entry["player"] else None)
        for entry in body["slots"]
    ]


def test_set_lineup_returns_the_saved_lineup(client, lineup_team):
    f = lineup_team
    p = f["players"]

    response = _put_lineup(
        client,
        f["team_id"],
        [
            {"player_id": p["Quinn QB"].id, "slot": "QB"},
            {"player_id": p["Rex RB"].id, "slot": "RB"},
            {"player_id": p["Tom TE"].id, "slot": "W/R/T"},
        ],
    )
    assert response.status_code == 200

    body = response.json()
    assert body["source"] == "manual"
    assert body["team_id"] == f["team_id"]
    assert _seats(body) == [
        ("QB", "Quinn QB"),
        ("RB", "Rex RB"),
        ("RB", None),
        ("WR", None),
        ("FLEX", "Tom TE"),
    ]
    assert {row["full_name"] for row in body["bench"]} == {"Rob RB", "Wade WR"}


def test_set_lineup_persists_week_zero_roster_slots(client, lineup_team, db_session):
    f = lineup_team
    _put_lineup(
        client,
        f["team_id"],
        [{"player_id": f["players"]["Rex RB"].id, "slot": "RB"}],
    )

    rows = db_session.query(RosterSlot).filter(RosterSlot.team_id == f["team_id"]).all()
    assert [(row.week, row.selected_position) for row in rows] == [(0, "RB")]


def test_set_lineup_replaces_the_previous_one(client, lineup_team):
    f = lineup_team
    p = f["players"]

    _put_lineup(client, f["team_id"], [{"player_id": p["Rex RB"].id, "slot": "RB"}])
    body = _put_lineup(
        client, f["team_id"], [{"player_id": p["Rob RB"].id, "slot": "RB"}]
    ).json()

    assert _seats(body) == [
        ("QB", None),
        ("RB", "Rob RB"),
        ("RB", None),
        ("WR", None),
        ("FLEX", None),
    ]


def test_saving_an_empty_lineup_is_the_same_as_clearing_it(client, lineup_team):
    f = lineup_team
    _put_lineup(client, f["team_id"], [{"player_id": f["players"]["Rex RB"].id, "slot": "RB"}])

    body = _put_lineup(client, f["team_id"], []).json()

    # Nothing is saved, so the computed lineup takes over again.
    assert body["source"] == "auto"
    assert sorted(_seats(body)) == sorted(
        [
            ("QB", "Quinn QB"),
            ("RB", "Rob RB"),
            ("RB", "Rex RB"),
            ("WR", "Wade WR"),
            ("FLEX", "Tom TE"),
        ]
    )


def test_set_lineup_rejects_another_teams_player(client, lineup_team):
    f = lineup_team
    response = _put_lineup(
        client,
        f["team_id"],
        [{"player_id": f["players"]["Rival RB"].id, "slot": "RB"}],
    )
    assert response.status_code == 400
    assert "not on My Team's roster" in response.json()["detail"]


def test_set_lineup_rejects_an_overfilled_slot(client, lineup_team):
    f = lineup_team
    p = f["players"]
    response = _put_lineup(
        client,
        f["team_id"],
        [
            {"player_id": p["Rob RB"].id, "slot": "RB"},
            {"player_id": p["Rex RB"].id, "slot": "RB"},
            {"player_id": p["Tom TE"].id, "slot": "RB"},
        ],
    )
    assert response.status_code == 400
    assert "only 2 RB slot(s)" in response.json()["detail"]


def test_set_lineup_rejects_an_ineligible_position(client, lineup_team):
    f = lineup_team
    response = _put_lineup(
        client,
        f["team_id"],
        [{"player_id": f["players"]["Rob RB"].id, "slot": "QB"}],
    )
    assert response.status_code == 400
    assert "cannot start in a QB slot" in response.json()["detail"]


def test_flex_accepts_rb_wr_te_but_not_qb(client, lineup_team):
    f = lineup_team
    p = f["players"]
    for name in ("Rob RB", "Wade WR", "Tom TE"):
        response = _put_lineup(
            client, f["team_id"], [{"player_id": p[name].id, "slot": "FLEX"}]
        )
        assert response.status_code == 200, name

    response = _put_lineup(
        client, f["team_id"], [{"player_id": p["Quinn QB"].id, "slot": "FLEX"}]
    )
    assert response.status_code == 400
    assert "cannot start in a FLEX slot" in response.json()["detail"]


def test_set_lineup_rejects_a_slot_the_league_does_not_have(client, lineup_team):
    f = lineup_team
    response = _put_lineup(
        client,
        f["team_id"],
        [{"player_id": f["players"]["Tom TE"].id, "slot": "TE"}],
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "not a starting slot" in detail
    assert "QB, RB, WR, FLEX" in detail


def test_set_lineup_rejects_a_bench_slot(client, lineup_team):
    f = lineup_team
    response = _put_lineup(
        client, f["team_id"], [{"player_id": f["players"]["Rob RB"].id, "slot": "BN"}]
    )
    assert response.status_code == 400
    assert "not a starting slot" in response.json()["detail"]


def test_set_lineup_rejects_a_duplicated_player(client, lineup_team):
    f = lineup_team
    rb = f["players"]["Rob RB"]
    response = _put_lineup(
        client,
        f["team_id"],
        [
            {"player_id": rb.id, "slot": "RB"},
            {"player_id": rb.id, "slot": "FLEX"},
        ],
    )
    assert response.status_code == 400
    assert "more than one slot" in response.json()["detail"]


def test_a_rejected_lineup_leaves_the_saved_one_alone(client, lineup_team, db_session):
    f = lineup_team
    p = f["players"]
    _put_lineup(client, f["team_id"], [{"player_id": p["Rex RB"].id, "slot": "RB"}])

    _put_lineup(client, f["team_id"], [{"player_id": p["Quinn QB"].id, "slot": "RB"}])

    rows = db_session.query(RosterSlot).filter(RosterSlot.team_id == f["team_id"]).all()
    assert [row.player_id for row in rows] == [p["Rex RB"].id]


def test_clear_lineup_reverts_to_auto(client, lineup_team, db_session):
    f = lineup_team
    p = f["players"]
    _put_lineup(client, f["team_id"], [{"player_id": p["Rex RB"].id, "slot": "RB"}])

    response = client.delete(f"/api/manual/teams/{f['team_id']}/lineup")
    assert response.status_code == 204
    assert db_session.query(RosterSlot).filter(
        RosterSlot.team_id == f["team_id"]
    ).count() == 0

    body = client.get(
        f"/api/leagues/{f['league_key']}/evaluate/teams/{f['team_id']}/lineup"
    ).json()
    assert body["source"] == "auto"


def test_clear_lineup_when_none_is_saved_is_still_204(client, lineup_team):
    assert (
        client.delete(f"/api/manual/teams/{lineup_team['team_id']}/lineup").status_code
        == 204
    )


def test_dropping_a_player_clears_their_lineup_slot(client, lineup_team, db_session):
    f = lineup_team
    p = f["players"]
    _put_lineup(
        client,
        f["team_id"],
        [
            {"player_id": p["Rex RB"].id, "slot": "RB"},
            {"player_id": p["Rob RB"].id, "slot": "RB"},
        ],
    )

    client.delete(f"/api/manual/teams/{f['team_id']}/roster/{p['Rex RB'].id}")

    rows = db_session.query(RosterSlot).filter(RosterSlot.team_id == f["team_id"]).all()
    assert [row.player_id for row in rows] == [p["Rob RB"].id]


def test_lineup_endpoints_reject_a_yahoo_team(client, db_session, yahoo_league):
    team = Team(team_key="461.l.999.t.1", league_id=yahoo_league.id, name="Rivals")
    db_session.add(team)
    db_session.commit()

    assert _put_lineup(client, team.id, []).status_code == 409
    assert client.delete(f"/api/manual/teams/{team.id}/lineup").status_code == 409


def test_lineup_endpoints_on_an_unknown_team_404(client):
    assert _put_lineup(client, 999, []).status_code == 404
    assert client.delete("/api/manual/teams/999/lineup").status_code == 404
