"""Sync-service unit tests. No network: the Yahoo league object is faked."""

from __future__ import annotations

from datetime import timedelta

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


def test_get_or_create_player_adopts_unlinked_rookie_by_name(db_session):
    # The crosswalk has no Yahoo ID for a fresh rookie yet: match by name +
    # position (suffix-insensitive) and adopt the Yahoo ID, no duplicate row.
    rookie = Player(full_name="Harold Fannin", position="TE", nfl_team="CLE", sleeper_id="12")
    db_session.add(rookie)
    db_session.commit()

    player = sync._get_or_create_player(
        db_session,
        {
            "player_id": "40100",
            "name": "Harold Fannin Jr.",
            "editorial_team_abbr": "Cle",
            "eligible_positions": ["TE"],
        },
    )
    db_session.commit()

    assert player.id == rookie.id
    assert player.yahoo_id == "40100"
    assert db_session.query(Player).count() == 1


def test_get_or_create_player_adopts_team_defense_by_team(db_session):
    defense = Player(full_name="New Orleans Saints", position="DEF", nfl_team="NO", sleeper_id="NO")
    db_session.add(defense)
    db_session.commit()

    player = sync._get_or_create_player(
        db_session,
        {
            "player_id": "100018",
            "name": "Saints",
            "editorial_team_abbr": "NO",
            "eligible_positions": ["DEF"],
        },
    )
    db_session.commit()

    assert player.id == defense.id
    assert player.yahoo_id == "100018"


def test_get_or_create_player_never_steals_another_yahoo_players_row(db_session):
    claimed = Player(full_name="Mike Williams", position="WR", yahoo_id="111")
    db_session.add(claimed)
    db_session.commit()

    player = sync._get_or_create_player(
        db_session,
        {"player_id": "222", "name": "Mike Williams", "eligible_positions": ["WR"]},
    )
    db_session.commit()

    assert player.id != claimed.id
    assert claimed.yahoo_id == "111"


def test_get_or_create_player_ambiguous_name_creates_new_row(db_session):
    db_session.add_all(
        [
            Player(full_name="Josh Johnson", position="QB", sleeper_id="1"),
            Player(full_name="Josh Johnson", position="QB", sleeper_id="2"),
        ]
    )
    db_session.commit()

    sync._get_or_create_player(
        db_session,
        {"player_id": "333", "name": "Josh Johnson", "eligible_positions": ["QB"]},
    )
    db_session.commit()

    assert db_session.query(Player).filter_by(yahoo_id="333").one().sleeper_id is None
    assert db_session.query(Player).count() == 3


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


# --- matchups: full season, flipped sides, TBD teams -------------------------


def _scoreboard(week: int, status: str, pairs: list[tuple[str, float | None, str, float | None]]):
    def team(key, pts):
        node = [[{"team_key": key}], {"team_points": {"total": pts}}]
        return {"team": node}

    matchups = {"count": len(pairs)}
    for i, (k1, p1, k2, p2) in enumerate(pairs):
        matchups[str(i)] = {
            "matchup": {
                "week": str(week),
                "status": status,
                "is_playoffs": "0",
                "0": {"teams": {"0": team(k1, p1), "1": team(k2, p2), "count": 2}},
            }
        }
    return {
        "fantasy_content": {
            "league": [{"league_key": "x"}, {"scoreboard": {"0": {"matchups": matchups}}}]
        }
    }


class FakeScoreboardLeague:
    def __init__(self, boards: dict[int, dict]) -> None:
        self.boards = boards
        self.weeks: list[int] = []

    def matchups(self, week=None):
        self.weeks.append(week)
        return self.boards.get(week, {})

    def settings(self):
        return {"end_week": "3"}


@pytest.fixture()
def two_teams(db_session, league):
    from app.models import Team

    a = Team(team_key="461.l.12345.t.1", league_id=league.id, name="A")
    b = Team(team_key="461.l.12345.t.2", league_id=league.id, name="B")
    db_session.add_all([a, b])
    db_session.commit()
    return a, b


def test_season_sync_pulls_every_week_and_stores_future_rows(db_session, league, two_teams):
    from app.models import Matchup

    a, b = two_teams
    league.settings_json = {"start_week": "1", "end_week": "3"}
    db_session.commit()
    boards = {
        1: _scoreboard(1, "postevent", [(a.team_key, 100.0, b.team_key, 90.0)]),
        2: _scoreboard(2, "preevent", [(a.team_key, 0.0, b.team_key, 0.0)]),
        3: _scoreboard(3, "preevent", [(a.team_key, None, "461.l.12345.t.99", None)]),  # TBD
    }
    lg = FakeScoreboardLeague(boards)

    sync._sync_season_matchups(db_session, league, lg)

    assert lg.weeks == [1, 2, 3]
    rows = db_session.query(Matchup).order_by(Matchup.week).all()
    assert [(r.week, r.status) for r in rows] == [(1, "postevent"), (2, "preevent")]


def test_matchup_upsert_survives_home_away_flip(db_session, league, two_teams):
    from app.models import Matchup

    a, b = two_teams
    lg = FakeScoreboardLeague(
        {1: _scoreboard(1, "midevent", [(a.team_key, 10.0, b.team_key, 20.0)])}
    )
    sync._sync_matchups(db_session, league, lg, 1)
    lg.boards = {1: _scoreboard(1, "postevent", [(b.team_key, 25.0, a.team_key, 15.0)])}
    sync._sync_matchups(db_session, league, lg, 1)

    rows = db_session.query(Matchup).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.home_team_id == a.id
    assert (row.home_points, row.away_points, row.status) == (15.0, 25.0, "postevent")


def test_sync_league_no_longer_syncs_transactions():
    import inspect

    assert not hasattr(sync, "_sync_transactions")
    assert "transactions" not in inspect.getsource(sync.sync_league)


# --- registry jobs -----------------------------------------------------------


def test_prune_sync_logs_removes_only_old_rows(db_session):
    from datetime import timedelta

    now = sync._utcnow()
    db_session.add_all(
        [
            SyncLog(resource="old", started_at=now - timedelta(days=31), status="success"),
            SyncLog(resource="new", started_at=now - timedelta(days=1), status="success"),
        ]
    )
    db_session.commit()

    assert sync.prune_sync_logs(db_session) == 1
    assert [r.resource for r in db_session.query(SyncLog)] == ["new"]


def test_sync_yahoo_leagues_isolates_failures(db_session, league, monkeypatch):
    other = League(league_key="461.l.2", game_key="461", name="Other", season=2026)
    db_session.add(other)
    db_session.commit()
    monkeypatch.setattr(sync, "make_session_context", lambda db: type("C", (), {"close": lambda s: None})())

    def fake_sync_league(db, key, full=True):
        if key == league.league_key:
            raise RuntimeError("boom")
        return {"status": "success", "week": 5, "errors": []}

    monkeypatch.setattr(sync, "sync_league", fake_sync_league)

    sync.sync_yahoo_leagues(db_session)

    log = db_session.query(SyncLog).filter_by(resource="yahoo_leagues").one()
    assert log.status == "partial"
    assert "boom" in log.message and "Other: success" in log.message


def test_sync_yahoo_leagues_refreshes_free_agents_about_hourly(
    db_session, league, monkeypatch
):
    monkeypatch.setattr(sync, "make_session_context", lambda db: type("C", (), {"close": lambda s: None})())
    calls: list[bool] = []

    def fake_sync_league(db, key, full=True):
        calls.append(full)
        return {"status": "success", "week": 5, "errors": []}

    monkeypatch.setattr(sync, "sync_league", fake_sync_league)

    sync.sync_yahoo_leagues(db_session)  # never synced: free agents due
    recent = SyncLog(
        resource="yahoo.free_agents",
        league_id=league.id,
        status="success",
        started_at=sync._utcnow() - timedelta(minutes=20),
    )
    db_session.add(recent)
    db_session.commit()
    sync.sync_yahoo_leagues(db_session)  # 20 minutes ago: skip
    recent.started_at = sync._utcnow() - timedelta(minutes=60)
    db_session.commit()
    sync.sync_yahoo_leagues(db_session)  # an hour ago: due again

    assert calls == [True, False, True]


def test_sync_yahoo_leagues_when_not_connected(db_session, league, monkeypatch):
    def refuse(db):
        raise RuntimeError("no token")

    monkeypatch.setattr(sync, "make_session_context", refuse)

    sync.sync_yahoo_leagues(db_session)  # must not raise

    log = db_session.query(SyncLog).filter_by(resource="yahoo_leagues").one()
    assert log.status == "error"
    assert "not connected" in log.message


def test_discover_job_logs_under_registry_key(db_session, monkeypatch):
    monkeypatch.setattr(sync, "discover_leagues", lambda db: [])
    sync.discover_yahoo_leagues(db_session)
    assert db_session.query(SyncLog).filter_by(resource="yahoo_discover").one().status == "success"


# --------------------------------------------------------------------------- #
# Yahoo as the source of truth: departures and the daily prune
# --------------------------------------------------------------------------- #


def _status_by_name(db) -> dict[str, str]:
    return {
        player.full_name: lp.status
        for lp, player in db.query(LeaguePlayer, Player).join(
            Player, Player.id == LeaguePlayer.player_id
        )
    }


def test_free_agent_sync_removes_players_yahoo_stopped_listing(db_session, league):
    sync._sync_free_agents(
        db_session, league, FakeLeague({"FA": [[_fa(1, "Alpha"), _fa(2, "Bravo")]], "W": []})
    )

    message = sync._sync_free_agents(
        db_session, league, FakeLeague({"FA": [[_fa(1, "Alpha")]], "W": []})
    )

    assert _status_by_name(db_session) == {"Alpha": "FA"}
    assert "1 no longer listed" in message


def test_free_agent_sync_removes_nothing_after_a_truncated_pull(db_session, league, monkeypatch):
    sync._sync_free_agents(
        db_session, league, FakeLeague({"FA": [[_fa(1, "Alpha"), _fa(2, "Bravo")]], "W": []})
    )
    monkeypatch.setattr(sync, "MAX_PLAYER_PAGES", 1)
    full_page = [_fa(i, f"Player {i}") for i in range(10, 10 + sync.PLAYERS_PER_PAGE)]

    sync._sync_free_agents(db_session, league, FakeLeague({"FA": [full_page, full_page], "W": []}))

    assert {"Alpha", "Bravo"} <= set(_status_by_name(db_session))


class FakeRosterLeague:
    def __init__(self, rosters: dict[str, list[dict] | Exception]) -> None:
        self.rosters = rosters

    def to_team(self, team_key):
        roster = self.rosters[team_key]

        class _Team:
            def roster(self, week=None):
                if isinstance(roster, Exception):
                    raise roster
                return roster

        return _Team()


def _slot(player_id: int, name: str, slot: str = "WR") -> dict:
    return {**_fa(player_id, name), "selected_position": slot}


def test_roster_sync_drops_players_on_no_roster(db_session, league, two_teams):
    a, b = two_teams
    sync._sync_rosters(
        db_session,
        league,
        FakeRosterLeague({a.team_key: [_slot(1, "Keeper"), _slot(2, "Cut")], b.team_key: [_slot(3, "Other")]}),
        week=1,
    )

    # "Cut" was dropped; "Other" was traded from B to A.
    message = sync._sync_rosters(
        db_session,
        league,
        FakeRosterLeague({a.team_key: [_slot(1, "Keeper"), _slot(3, "Other")], b.team_key: []}),
        week=1,
    )

    on_team = {
        player.full_name: lp.on_team_id
        for lp, player in db_session.query(LeaguePlayer, Player).join(
            Player, Player.id == LeaguePlayer.player_id
        )
    }
    assert on_team == {"Keeper": a.id, "Other": a.id}
    assert "1 dropped" in message


def test_roster_sync_failure_drops_nobody(db_session, league, two_teams):
    a, b = two_teams
    sync._sync_rosters(
        db_session,
        league,
        FakeRosterLeague({a.team_key: [_slot(1, "Keeper")], b.team_key: [_slot(2, "Other")]}),
        week=1,
    )

    with pytest.raises(RuntimeError):
        sync._sync_rosters(
            db_session,
            league,
            FakeRosterLeague({a.team_key: [], b.team_key: RuntimeError("yahoo 500")}),
            week=1,
        )

    assert set(_status_by_name(db_session)) == {"Keeper", "Other"}


def test_daily_prune_removes_unreferenced_players(db_session, league, monkeypatch):
    listed = Player(full_name="Listed", position="WR", yahoo_id="1")
    gone = Player(full_name="Gone", position="WR", yahoo_id="2")
    db_session.add_all([listed, gone])
    db_session.flush()
    db_session.add(LeaguePlayer(league_id=league.id, player_id=listed.id, status="FA"))
    db_session.commit()

    assert "skipped" in sync.prune_unreferenced_players(db_session)  # too few to trust
    assert db_session.query(Player).count() == 2

    monkeypatch.setattr(sync, "MIN_PLAYERS_FOR_PRUNE", 1)
    assert "pruned 1" in sync.prune_unreferenced_players(db_session)
    assert [p.full_name for p in db_session.query(Player).all()] == ["Listed"]
