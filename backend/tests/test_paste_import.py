"""Importing manual teams/rosters/lineups from a pasted Yahoo rosters page."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.models import (
    League,
    LeaguePlayer,
    Player,
    Projection,
    RosterSlot,
    Team,
    TradeValue,
)
from app.services import evaluator
from app.services.paste_import import parse_roster_paste

# A copy/paste of Yahoo's "League > Rosters" page: preamble junk, two teams,
# the viewer's own team prefixed with "Your ", every slot flavour the league
# has (including W/R/T, Q/W/R/T and IR-R), a defense listed by nickname, an
# unfilled slot, and the name+noise repeat line after every player.
SAMPLE = """\
Team
Position
Week 1

Your Suck my Dak!!\x20

Pos    Player
QB\x20\x20\x20\x20
Lamar Jackson
Lamar JacksonVideo ForecastPlayer Note
Bal - QB
Sun 12:00 pm @ Ind
RB\x20\x20\x20\x20
Bijan Robinson
Bijan RobinsonVideo ForecastPlayer Note
Atl - RB
Sun 12:00 pm @ Pit
RB\x20\x20\x20\x20
Kenneth Walker III
Kenneth Walker IIIQVideo ForecastNew Player Note
Sea - RB
Sun 3:05 pm vs SF
WR\x20\x20\x20\x20
Ja'Marr Chase
Ja'Marr ChaseVideo ForecastPlayer Note
Cin - WR
Sun 12:00 pm vs Cle
TE\x20\x20\x20\x20
Trey McBride
Trey McBrideVideo ForecastNo new player Notes
Ari - TE
Mon 7:15 pm @ NO
W/R/T\x20\x20\x20\x20
Jaxon Smith-Njigba
Jaxon Smith-NjigbaVideo ForecastPlayer Note
Sea - WR
Sun 3:05 pm vs SF
Q/W/R/T\x20\x20\x20\x20
Baker Mayfield
Baker MayfieldVideo ForecastPlayer Note
TB - QB
Sun 12:00 pm vs Atl
K\x20\x20\x20\x20
Harrison Butker
Harrison ButkerVideo ForecastPlayer Note
KC - K
Sun 3:25 pm @ LV
DEF\x20\x20\x20\x20
Eagles
Eagles DefenseVideo Forecast
Phi - DEF
Thu 7:20 pm vs Dal
BN\x20\x20\x20\x20
George Kittle
George KittleQVideo ForecastNew Player Note
SF - TE
Thu 7:35 pm @ LAR
IR-R\x20\x20\x20\x20
Isiah Pacheco
Isiah PachecoIRVideo ForecastPlayer Note
KC - RB
Sun 3:25 pm @ LV

Gridiron Gang\x20

Pos    Player
QB\x20\x20\x20\x20
Jalen Hurts
Jalen HurtsVideo ForecastPlayer Note
Phi - QB
Thu 7:20 pm vs Dal
RB\x20\x20\x20\x20
Saquon Barkley
Saquon BarkleyVideo ForecastPlayer Note
Phi - RB
Thu 7:20 pm vs Dal
RB\x20\x20\x20\x20
(Empty)
WR\x20\x20\x20\x20
Amon-Ra St. Brown
Amon-Ra St. BrownVideo ForecastPlayer Note
Det - WR
Sun 12:00 pm vs NO
"""


# --- parser ----------------------------------------------------------------


def test_parses_every_team_in_the_paste():
    teams = parse_roster_paste(SAMPLE)
    assert [team.name for team in teams] == ["Your Suck my Dak!!", "Gridiron Gang"]


def test_preamble_junk_does_not_become_a_team():
    teams = parse_roster_paste(SAMPLE)
    assert len(teams) == 2
    # "Week 1" is the line before the team name, not the team name.
    assert teams[0].name == "Your Suck my Dak!!"


def test_player_names_are_the_clean_line_only():
    first = parse_roster_paste(SAMPLE)[0]
    assert [player.name for player in first.players] == [
        "Lamar Jackson",
        "Bijan Robinson",
        "Kenneth Walker III",
        "Ja'Marr Chase",
        "Trey McBride",
        "Jaxon Smith-Njigba",
        "Baker Mayfield",
        "Harrison Butker",
        "Eagles",
        "George Kittle",
        "Isiah Pacheco",
    ]


def test_slots_are_normalized():
    first = parse_roster_paste(SAMPLE)[0]
    assert [player.slot for player in first.players] == [
        "QB",
        "RB",
        "RB",
        "WR",
        "TE",
        "FLEX",
        "SUPERFLEX",
        "K",
        "DEF",
        "BN",
        "IR",
    ]


def test_position_hints_come_from_the_team_dash_position_line():
    first = parse_roster_paste(SAMPLE)[0]
    hints = {player.name: player.position_hint for player in first.players}
    assert hints["Lamar Jackson"] == "QB"
    # The FLEX/SUPERFLEX seats keep the player's real position, not the slot.
    assert hints["Jaxon Smith-Njigba"] == "WR"
    assert hints["Baker Mayfield"] == "QB"
    assert hints["Eagles"] == "DEF"
    assert hints["Isiah Pacheco"] == "RB"


def test_empty_slot_marker_yields_no_player():
    second = parse_roster_paste(SAMPLE)[1]
    assert [player.name for player in second.players] == [
        "Jalen Hurts",
        "Saquon Barkley",
        "Amon-Ra St. Brown",
    ]
    assert [player.slot for player in second.players] == ["QB", "RB", "WR"]


@pytest.mark.parametrize(
    "label, expected",
    [
        ("W/R", "FLEX"),
        ("WR/RB/TE", "FLEX"),
        ("Q/W/R/T", "SUPERFLEX"),
        ("OP", "SUPERFLEX"),
        ("D/ST", "DEF"),
        ("PK", "K"),
        ("IR", "IR"),
        ("IR-R", "IR"),
        ("PUP-R", "IR"),
        ("NA", "NA"),
    ],
)
def test_slot_label_variants(label, expected):
    text = f"Team A\nPos  Player\n{label}\nSome Player\nBuf - RB\n"
    player = parse_roster_paste(text)[0].players[0]
    assert player.slot == expected


def test_split_pos_and_player_header_is_tolerated():
    text = "Team A\nPos\nPlayer\nQB\nJosh Allen\nBuf - QB\n"
    teams = parse_roster_paste(text)
    assert teams[0].name == "Team A"
    assert teams[0].players[0].name == "Josh Allen"


def test_team_with_no_players_still_parses():
    text = "Team A\nPos  Player\n\nTeam B\nPos  Player\nQB\nJosh Allen\n"
    teams = parse_roster_paste(text)
    assert [team.name for team in teams] == ["Team A", "Team B"]
    assert teams[0].players == []


def test_missing_position_line_leaves_no_hint():
    text = "Team A\nPos  Player\nWR\nSome Rookie\n"
    player = parse_roster_paste(text)[0].players[0]
    assert player.position_hint is None


def test_text_without_a_roster_header_raises():
    with pytest.raises(ValueError, match="No teams found"):
        parse_roster_paste("Just some notes I copied by accident")


def test_empty_text_raises():
    with pytest.raises(ValueError):
        parse_roster_paste("")


# --- import fixtures -------------------------------------------------------


IMPORT_SLOTS = {
    "QB": 1,
    "RB": 2,
    "WR": 1,
    "TE": 1,
    "FLEX": 1,
    "SUPERFLEX": 1,
    "K": 1,
    "DEF": 1,
    "BN": 3,
    "IR": 1,
}

POOL = [
    ("Lamar Jackson", "QB", "BAL"),
    ("Bijan Robinson", "RB", "ATL"),
    ("Kenneth Walker III", "RB", "SEA"),
    ("Ja'Marr Chase", "WR", "CIN"),
    ("Trey McBride", "TE", "ARI"),
    ("Jaxon Smith-Njigba", "WR", "SEA"),
    ("Baker Mayfield", "QB", "TB"),
    ("Harrison Butker", "K", "KC"),
    ("Philadelphia Eagles", "DEF", "PHI"),
    ("George Kittle", "TE", "SF"),
    ("Isiah Pacheco", "RB", "KC"),
    ("Jalen Hurts", "QB", "PHI"),
    ("Saquon Barkley", "RB", "PHI"),
    ("Amon-Ra St. Brown", "WR", "DET"),
]


@pytest.fixture()
def pool(db_session) -> dict[str, Player]:
    players = {
        name: Player(full_name=name, position=position, nfl_team=team)
        for name, position, team in POOL
    }
    db_session.add_all(players.values())
    db_session.commit()
    return players


@pytest.fixture()
def league_key(client) -> str:
    response = client.post(
        "/api/manual/leagues",
        json={"name": "Paste League", "season": 2026, "roster_slots": IMPORT_SLOTS},
    )
    return response.json()["league_key"]


def _import(client, league_key: str, text: str):
    return client.post(
        f"/api/manual/leagues/{league_key}/import-roster-paste", json={"text": text}
    )


def _paste(*teams: tuple[str, list[tuple[str, str, str | None]]]) -> str:
    """Build a rosters-page paste from ``(team name, [(slot, name, hint)])``."""
    lines: list[str] = ["Team", "Week 1", ""]
    for name, entries in teams:
        lines += [name, "", "Pos    Player"]
        for slot, player_name, hint in entries:
            lines.append(f"{slot}    ")
            lines.append(player_name)
            lines.append(f"{player_name}Video ForecastPlayer Note")
            if hint:
                lines.append(hint)
            lines.append("Sun 12:00 pm @ Ind")
        lines.append("")
    return "\n".join(lines)


def _roster(db_session, team_id: int) -> set[str]:
    rows = (
        db_session.query(Player.full_name)
        .join(LeaguePlayer, LeaguePlayer.player_id == Player.id)
        .filter(LeaguePlayer.on_team_id == team_id)
        .all()
    )
    return {row[0] for row in rows}


def _lineup(db_session, team_id: int) -> dict[str, str]:
    rows = (
        db_session.query(RosterSlot, Player)
        .join(Player, Player.id == RosterSlot.player_id)
        .filter(
            RosterSlot.team_id == team_id,
            RosterSlot.week == evaluator.MANUAL_LINEUP_WEEK,
        )
        .all()
    )
    return {player.full_name: slot.selected_position for slot, player in rows}


# --- import ----------------------------------------------------------------


def test_import_creates_teams_rosters_and_lineups(client, db_session, pool, league_key):
    response = _import(client, league_key, SAMPLE)
    assert response.status_code == 200
    body = response.json()

    assert [team["team"] for team in body["teams"]] == [
        "Your Suck my Dak!!",
        "Gridiron Gang",
    ]
    assert all(team["created"] for team in body["teams"])
    assert body["total_unmatched"] == 0

    first = body["teams"][0]
    assert (first["added"], first["removed"], first["kept"]) == (11, 0, 0)
    assert first["lineup_set"] is True

    teams = {team.name: team for team in db_session.query(Team).all()}
    assert set(teams) == {"Your Suck my Dak!!", "Gridiron Gang"}
    assert teams["Your Suck my Dak!!"].team_key.startswith("manual.")
    assert ".t." in teams["Your Suck my Dak!!"].team_key

    assert "George Kittle" in _roster(db_session, teams["Your Suck my Dak!!"].id)
    assert _roster(db_session, teams["Gridiron Gang"].id) == {
        "Jalen Hurts",
        "Saquon Barkley",
        "Amon-Ra St. Brown",
    }


def test_import_sets_the_lineup_including_flex_and_superflex(
    client, db_session, pool, league_key
):
    _import(client, league_key, SAMPLE)
    team = db_session.query(Team).filter(Team.name == "Your Suck my Dak!!").one()

    assert _lineup(db_session, team.id) == {
        "Lamar Jackson": "QB",
        "Bijan Robinson": "RB",
        "Kenneth Walker III": "RB",
        "Ja'Marr Chase": "WR",
        "Trey McBride": "TE",
        "Jaxon Smith-Njigba": "FLEX",
        "Baker Mayfield": "SUPERFLEX",
        "Harrison Butker": "K",
        "Philadelphia Eagles": "DEF",
    }


def test_bench_and_ir_players_are_rostered_but_not_started(
    client, db_session, pool, league_key
):
    _import(client, league_key, SAMPLE)
    team = db_session.query(Team).filter(Team.name == "Your Suck my Dak!!").one()

    roster = _roster(db_session, team.id)
    assert {"George Kittle", "Isiah Pacheco"} <= roster
    assert "George Kittle" not in _lineup(db_session, team.id)
    assert "Isiah Pacheco" not in _lineup(db_session, team.id)


def test_defense_matches_by_nickname(client, db_session, pool, league_key):
    _import(client, league_key, SAMPLE)
    team = db_session.query(Team).filter(Team.name == "Your Suck my Dak!!").one()
    assert "Philadelphia Eagles" in _roster(db_session, team.id)


def test_import_matches_an_existing_team_case_insensitively(
    client, db_session, pool, league_key
):
    created = client.post(
        f"/api/manual/leagues/{league_key}/teams", json={"name": "gridiron gang"}
    ).json()

    text = _paste(("Gridiron Gang", [("QB", "Jalen Hurts", "Phi - QB")]))
    body = _import(client, league_key, text).json()

    assert body["teams"][0]["created"] is False
    assert db_session.query(Team).count() == 1
    assert _roster(db_session, created["id"]) == {"Jalen Hurts"}


def test_import_matches_an_existing_team_through_the_your_prefix(
    client, db_session, pool, league_key
):
    created = client.post(
        f"/api/manual/leagues/{league_key}/teams", json={"name": "Suck my Dak!!"}
    ).json()

    text = _paste(("Your Suck my Dak!!", [("QB", "Lamar Jackson", "Bal - QB")]))
    body = _import(client, league_key, text).json()

    assert body["teams"][0]["created"] is False
    assert body["teams"][0]["team"] == "Suck my Dak!!"
    assert db_session.query(Team).count() == 1
    assert _roster(db_session, created["id"]) == {"Lamar Jackson"}


def test_import_replaces_a_roster_and_drops_departed_players(
    client, db_session, pool, league_key
):
    text = _paste(
        (
            "Gridiron Gang",
            [
                ("QB", "Jalen Hurts", "Phi - QB"),
                ("RB", "Saquon Barkley", "Phi - RB"),
            ],
        )
    )
    _import(client, league_key, text)
    team = db_session.query(Team).filter(Team.name == "Gridiron Gang").one()
    assert _lineup(db_session, team.id)["Saquon Barkley"] == "RB"

    refreshed = _paste(
        (
            "Gridiron Gang",
            [
                ("QB", "Jalen Hurts", "Phi - QB"),
                ("RB", "Bijan Robinson", "Atl - RB"),
            ],
        )
    )
    body = _import(client, league_key, refreshed).json()

    report = body["teams"][0]
    assert (report["added"], report["removed"], report["kept"]) == (1, 1, 1)
    assert _roster(db_session, team.id) == {"Jalen Hurts", "Bijan Robinson"}
    # The dropped player must not linger in the saved lineup.
    assert "Saquon Barkley" not in _lineup(db_session, team.id)


def test_player_switching_teams_within_one_paste(client, db_session, pool, league_key):
    initial = _paste(
        ("Team A", [("RB", "Saquon Barkley", "Phi - RB")]),
        ("Team B", [("RB", "Bijan Robinson", "Atl - RB")]),
    )
    _import(client, league_key, initial)
    team_a = db_session.query(Team).filter(Team.name == "Team A").one()
    team_b = db_session.query(Team).filter(Team.name == "Team B").one()

    # Barkley moves to B, and B is listed *first*: removals must all happen
    # before additions or the move depends on paste order.
    swapped = _paste(
        (
            "Team B",
            [
                ("RB", "Saquon Barkley", "Phi - RB"),
                ("RB", "Bijan Robinson", "Atl - RB"),
            ],
        ),
        ("Team A", [("RB", "Jalen Hurts", "Phi - QB")]),
    )
    body = _import(client, league_key, swapped).json()

    assert _roster(db_session, team_b.id) == {"Saquon Barkley", "Bijan Robinson"}
    assert _roster(db_session, team_a.id) == {"Jalen Hurts"}
    assert db_session.query(Team).count() == 2

    reports = {team["team"]: team for team in body["teams"]}
    assert reports["Team B"]["added"] == 1
    assert reports["Team A"]["removed"] == 1
    assert "Saquon Barkley" not in _lineup(db_session, team_a.id)


def test_player_moving_from_a_team_absent_from_the_paste(
    client, db_session, pool, league_key
):
    _import(
        client,
        league_key,
        _paste(
            ("Team A", [("RB", "Saquon Barkley", "Phi - RB")]),
            ("Team B", [("RB", "Bijan Robinson", "Atl - RB")]),
        ),
    )
    team_a = db_session.query(Team).filter(Team.name == "Team A").one()
    team_b = db_session.query(Team).filter(Team.name == "Team B").one()

    # Only Team B is pasted; Team A keeps existing but loses the player.
    _import(
        client,
        league_key,
        _paste(
            (
                "Team B",
                [
                    ("RB", "Saquon Barkley", "Phi - RB"),
                    ("RB", "Bijan Robinson", "Atl - RB"),
                ],
            )
        ),
    )

    assert db_session.query(Team).count() == 2
    assert _roster(db_session, team_a.id) == set()
    assert _roster(db_session, team_b.id) == {"Saquon Barkley", "Bijan Robinson"}
    assert _lineup(db_session, team_a.id) == {}


def test_teams_missing_from_the_paste_are_never_deleted(
    client, db_session, pool, league_key
):
    other = client.post(
        f"/api/manual/leagues/{league_key}/teams", json={"name": "Untouched"}
    ).json()

    _import(client, league_key, _paste(("Team A", [("QB", "Jalen Hurts", "Phi - QB")])))

    assert db_session.query(Team).filter(Team.id == other["id"]).one_or_none() is not None


def test_unmatched_players_are_reported_not_fatal(client, db_session, pool, league_key):
    text = _paste(
        (
            "Team A",
            [
                ("QB", "Jalen Hurts", "Phi - QB"),
                ("RB", "Nobody McUnknown", "Cle - RB"),
                ("WR", "Ghost Receiver", "NYJ - WR"),
            ],
        )
    )
    body = _import(client, league_key, text).json()

    report = body["teams"][0]
    assert report["unmatched"] == ["Nobody McUnknown", "Ghost Receiver"]
    assert body["total_unmatched"] == 2
    assert report["added"] == 1

    team = db_session.query(Team).filter(Team.name == "Team A").one()
    assert _roster(db_session, team.id) == {"Jalen Hurts"}
    assert report["lineup_set"] is True


def test_same_name_players_prefer_the_one_with_a_trade_value(
    client, db_session, pool, league_key
):
    starter = Player(full_name="Mike Williams", position="WR", nfl_team="NYJ")
    db_session.add_all(
        [starter, Player(full_name="Mike Williams", position="WR", nfl_team="FA")]
    )
    db_session.commit()
    db_session.add(
        TradeValue(
            player_id=starter.id,
            source=evaluator.TRADE_VALUE_SOURCE,
            format="redraft",
            value=1200.0,
            fetched_at=datetime(2026, 9, 1),
        )
    )
    db_session.commit()

    text = _paste(("Team A", [("WR", "Mike Williams", "NYJ - WR")]))
    body = _import(client, league_key, text).json()

    assert body["total_unmatched"] == 0
    team = db_session.query(Team).filter(Team.name == "Team A").one()
    rostered = db_session.query(LeaguePlayer).filter(
        LeaguePlayer.on_team_id == team.id
    ).one()
    assert rostered.player_id == starter.id


def test_same_name_players_fall_back_to_the_projected_one(
    client, db_session, pool, league_key
):
    projected = Player(full_name="Josh Palmer", position="WR", nfl_team="BUF")
    db_session.add_all(
        [projected, Player(full_name="Josh Palmer", position="WR", nfl_team="FA")]
    )
    db_session.commit()
    db_session.add(
        Projection(
            player_id=projected.id,
            source="fantasypros",
            season=2026,
            week=None,
            stat_json={"rec": 60},
            fetched_at=datetime(2026, 9, 1),
        )
    )
    db_session.commit()

    text = _paste(("Team A", [("WR", "Josh Palmer", "Buf - WR")]))
    _import(client, league_key, text)

    team = db_session.query(Team).filter(Team.name == "Team A").one()
    rostered = db_session.query(LeaguePlayer).filter(
        LeaguePlayer.on_team_id == team.id
    ).one()
    assert rostered.player_id == projected.id


def test_hopelessly_ambiguous_names_are_reported_unmatched(
    client, db_session, pool, league_key
):
    db_session.add_all(
        [
            Player(full_name="Mike Evans", position="WR", nfl_team="TB"),
            Player(full_name="Mike Evans", position="WR", nfl_team="FA"),
        ]
    )
    db_session.commit()

    text = _paste(("Team A", [("WR", "Mike Evans", "TB - WR")]))
    body = _import(client, league_key, text).json()

    assert body["teams"][0]["unmatched"] == ["Mike Evans"]


def test_lineup_validation_failure_is_reported_and_roster_still_imports(
    client, db_session, pool, league_key
):
    # Two QB seats in a league that only has one.
    text = _paste(
        (
            "Team A",
            [
                ("QB", "Jalen Hurts", "Phi - QB"),
                ("QB", "Lamar Jackson", "Bal - QB"),
            ],
        )
    )
    body = _import(client, league_key, text).json()

    report = body["teams"][0]
    assert report["lineup_set"] is False
    assert "only 1 QB slot(s)" in report["lineup_error"]
    assert report["added"] == 2

    team = db_session.query(Team).filter(Team.name == "Team A").one()
    assert _roster(db_session, team.id) == {"Jalen Hurts", "Lamar Jackson"}
    assert _lineup(db_session, team.id) == {}


def test_import_rejects_unparseable_text_with_400(client, league_key):
    response = _import(client, league_key, "nothing useful here")
    assert response.status_code == 400
    assert "No teams found" in response.json()["detail"]


def test_import_rejects_a_yahoo_league(client, db_session):
    league = League(
        league_key="461.l.999",
        game_key="461",
        name="Real Yahoo League",
        season=2026,
        source="yahoo",
    )
    db_session.add(league)
    db_session.commit()

    response = _import(client, league.league_key, SAMPLE)
    assert response.status_code == 409


def test_import_rejects_an_unknown_league(client):
    assert _import(client, "manual.999", SAMPLE).status_code == 404


def test_reimporting_the_same_paste_is_idempotent(client, db_session, pool, league_key):
    _import(client, league_key, SAMPLE)
    body = _import(client, league_key, SAMPLE).json()

    for report in body["teams"]:
        assert report["created"] is False
        assert (report["added"], report["removed"]) == (0, 0)
    assert db_session.query(Team).count() == 2

    team = db_session.query(Team).filter(Team.name == "Your Suck my Dak!!").one()
    assert len(_lineup(db_session, team.id)) == 9
