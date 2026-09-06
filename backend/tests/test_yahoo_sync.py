"""Sync-service unit tests. No network: the Yahoo league object is faked."""

from __future__ import annotations

import pytest

from app.models import League, LeaguePlayer, Player, SyncLog
from app.services.yahoo import sync


@pytest.fixture(autouse=True)
def no_paging_delay(monkeypatch):
    monkeypatch.setattr(sync, "PAGE_DELAY_SECONDS", 0)


@pytest.fixture()
def league(db_session) -> League:
    row = League(
        league_key="461.l.12345",
        game_key="461",
        name="Test League",
        season=2026,
        num_teams=12,
        scoring_type="head",
        current_week=1,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


class FakeLeague:
    """Stands in for ``yahoo_fantasy_api.League`` for the paging helpers.

    ``_iter_player_pages`` only touches ``league_id``, ``yhandler`` and
    ``_players_from_page``, so faking those three is enough.
    """

    def __init__(self, pages_by_status: dict[str, list[list[dict]]]) -> None:
        self.league_id = "461.l.12345"
        self.pages_by_status = pages_by_status
        self.yhandler = self
        self.calls: list[tuple[str, int]] = []

    def get_players_raw(self, league_id, start, status, position=None):
        self.calls.append((status, start))
        pages = self.pages_by_status.get(status, [])
        index = start // sync.PLAYERS_PER_PAGE
        return pages[index] if index < len(pages) else []

    def _players_from_page(self, page):
        return (len(page), list(page))


def _fa(player_id: int, name: str, position: str = "WR", pct: int = 50) -> dict:
    return {
        "player_id": player_id,
        "name": name,
        "editorial_team_abbr": "SF",
        "eligible_positions": [position],
        "position_type": "O",
        "status": "",
        "percent_owned": pct,
    }


# --------------------------------------------------------------------------- #
# _get_or_create_player
# --------------------------------------------------------------------------- #


def test_get_or_create_player_creates_new_row(db_session):
    player = sync._get_or_create_player(
        db_session,
        {
            "player_id": 31000,
            "name": "Brock Purdy",
            "editorial_team_abbr": "SF",
            "eligible_positions": ["QB"],
            "status": "Q",
        },
    )
    db_session.commit()

    assert player.id is not None
    assert player.yahoo_id == "31000"
    assert player.full_name == "Brock Purdy"
    assert player.position == "QB"
    assert player.nfl_team == "SF"
    assert player.injury_status == "Q"
    # Other platform IDs stay NULL until the nflverse crosswalk lands.
    assert player.sleeper_id is None
    assert player.espn_id is None
    assert db_session.query(Player).count() == 1


def test_get_or_create_player_matches_existing_yahoo_id(db_session):
    existing = Player(full_name="Old Name", yahoo_id="31000", position="QB")
    db_session.add(existing)
    db_session.commit()

    player = sync._get_or_create_player(
        db_session,
        {
            "player_id": "31000",
            "name": "Brock Purdy",
            "editorial_team_abbr": "SF",
            "eligible_positions": ["QB"],
        },
    )
    db_session.commit()

    assert player.id == existing.id
    assert player.full_name == "Brock Purdy"  # refreshed from Yahoo
    assert db_session.query(Player).count() == 1


def test_get_or_create_player_ignores_slot_positions(db_session):
    player = sync._get_or_create_player(
        db_session,
        {
            "player_id": 1,
            "name": "Flex Guy",
            "eligible_positions": ["W/R/T", "BN", "RB"],
        },
    )
    assert player.position == "RB"


def test_get_or_create_player_handles_nested_name_and_bye(db_session):
    player = sync._get_or_create_player(
        db_session,
        {
            "player_id": 42,
            "name": {"full": "Detail Guy", "first": "Detail"},
            "display_position": "WR,TE",
            "bye_weeks": {"week": "9"},
        },
    )
    assert player.full_name == "Detail Guy"
    assert player.position == "WR"
    assert player.bye_week == 9


def test_get_or_create_player_without_yahoo_id(db_session):
    player = sync._get_or_create_player(db_session, {"name": "Mystery Man"})
    db_session.commit()
    assert player.yahoo_id is None
    assert player.full_name == "Mystery Man"


# --------------------------------------------------------------------------- #
# free agents
# --------------------------------------------------------------------------- #


def test_free_agent_sync_is_idempotent(db_session, league):
    fake = FakeLeague(
        {
            "FA": [[_fa(1, "Alpha"), _fa(2, "Bravo"), _fa(3, "Charlie")]],
            "W": [[_fa(4, "Delta", pct=12)]],
        }
    )

    first = sync._sync_free_agents(db_session, league, fake)
    assert "4 available player(s)" in first

    # Running the sync a second time must update, not duplicate.
    sync._sync_free_agents(db_session, league, fake)

    assert db_session.query(Player).count() == 4
    assert db_session.query(LeaguePlayer).count() == 4

    statuses = {
        player.full_name: lp.status
        for lp, player in db_session.query(LeaguePlayer, Player).join(
            Player, Player.id == LeaguePlayer.player_id
        )
    }
    assert statuses == {
        "Alpha": "FA",
        "Bravo": "FA",
        "Charlie": "FA",
        "Delta": "W",
    }

    delta = (
        db_session.query(LeaguePlayer)
        .join(Player, Player.id == LeaguePlayer.player_id)
        .filter(Player.full_name == "Delta")
        .one()
    )
    assert delta.percent_owned == 12
    assert delta.on_team_id is None


def test_free_agent_sync_pages_until_short_page(db_session, league):
    full_page = [_fa(i, f"Player {i}") for i in range(sync.PLAYERS_PER_PAGE)]
    short_page = [_fa(100, "Last Guy")]
    fake = FakeLeague({"FA": [full_page, short_page], "W": []})

    sync._sync_free_agents(db_session, league, fake)

    assert ("FA", 0) in fake.calls
    assert ("FA", sync.PLAYERS_PER_PAGE) in fake.calls
    assert db_session.query(LeaguePlayer).count() == sync.PLAYERS_PER_PAGE + 1


def test_free_agent_sync_flips_status_on_repeat(db_session, league):
    fake_fa = FakeLeague({"FA": [[_fa(1, "Alpha")]], "W": []})
    sync._sync_free_agents(db_session, league, fake_fa)

    fake_w = FakeLeague({"FA": [], "W": [[_fa(1, "Alpha")]]})
    sync._sync_free_agents(db_session, league, fake_w)

    rows = db_session.query(LeaguePlayer).all()
    assert len(rows) == 1
    assert rows[0].status == "W"


# --------------------------------------------------------------------------- #
# league upsert + matchup parsing + sync log
# --------------------------------------------------------------------------- #


def test_upsert_league_preserves_is_keeper(db_session, league):
    league.is_keeper = True
    db_session.commit()

    sync._upsert_league(
        db_session,
        league.league_key,
        {
            "name": "Renamed League",
            "season": "2026",
            "num_teams": "10",
            "scoring_type": "headpoint",
            "current_week": "4",
            "game_code": "nfl",
        },
    )
    db_session.commit()

    refreshed = db_session.query(League).one()
    assert refreshed.is_keeper is True  # owner-managed, never clobbered by sync
    assert refreshed.name == "Renamed League"
    assert refreshed.num_teams == 10
    assert refreshed.current_week == 4
    assert refreshed.game_key == "461"
    assert refreshed.settings_json["scoring_type"] == "headpoint"


def test_parse_matchups():
    raw = {
        "fantasy_content": {
            "league": [
                {"league_key": "461.l.12345"},
                {
                    "scoreboard": {
                        "0": {
                            "matchups": {
                                "0": {
                                    "matchup": {
                                        "week": "3",
                                        "status": "postevent",
                                        "is_playoffs": "0",
                                        "0": {
                                            "teams": {
                                                "0": {
                                                    "team": [
                                                        [{"team_key": "461.l.1.t.1"}],
                                                        {
                                                            "team_points": {
                                                                "total": "101.5"
                                                            }
                                                        },
                                                    ]
                                                },
                                                "1": {
                                                    "team": [
                                                        [{"team_key": "461.l.1.t.2"}],
                                                        {
                                                            "team_points": {
                                                                "total": "88.25"
                                                            }
                                                        },
                                                    ]
                                                },
                                                "count": 2,
                                            }
                                        },
                                    }
                                },
                                "count": 1,
                            }
                        }
                    }
                },
            ]
        }
    }

    parsed = sync._parse_matchups(raw)
    assert len(parsed) == 1
    assert parsed[0]["week"] == 3
    assert parsed[0]["status"] == "postevent"
    assert parsed[0]["is_playoffs"] is False
    assert parsed[0]["teams"] == [
        ("461.l.1.t.1", 101.5),
        ("461.l.1.t.2", 88.25),
    ]


def test_parse_matchups_tolerates_junk():
    assert sync._parse_matchups({}) == []
    assert sync._parse_matchups({"fantasy_content": {"league": []}}) == []


def test_sync_log_records_success(db_session):
    with sync._sync_log(db_session, "test.resource") as log:
        log.message = "all good"

    row = db_session.query(SyncLog).one()
    assert row.resource == "test.resource"
    assert row.status == "success"
    assert row.message == "all good"
    assert row.finished_at is not None


def test_sync_log_records_failure(db_session):
    with pytest.raises(ValueError):
        with sync._sync_log(db_session, "test.resource"):
            raise ValueError("boom")

    row = db_session.query(SyncLog).one()
    assert row.status == "error"
    assert "boom" in row.message


def test_run_step_swallows_failures(db_session):
    errors: list[str] = []

    result = sync._run_step(
        db_session,
        "test.step",
        None,
        lambda: (_ for _ in ()).throw(RuntimeError("yahoo 500")),
        errors,
    )

    assert result is None
    assert errors and "yahoo 500" in errors[0]
    assert db_session.query(SyncLog).one().status == "error"


def test_current_nfl_season():
    from datetime import date

    assert sync.current_nfl_season(date(2026, 9, 6)) == 2026
    assert sync.current_nfl_season(date(2027, 1, 10)) == 2026
    assert sync.current_nfl_season(date(2026, 3, 1)) == 2026
