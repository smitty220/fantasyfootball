"""NFL schedule ingestion, bye-week recompute, and the evaluator lookups.

No network: ``_fetch_csv_text`` is monkeypatched everywhere, and the autouse
fixture below makes a test that forgets to do so fail loudly rather than
downloading 2MB from nflverse.
"""

from __future__ import annotations

import logging

import pytest

from app.models import NflGame, Player, SyncLog
from app.services import nfl_schedule

#: The columns we actually read, in the order the live file carries them.
#: (The real file has 46 columns; DictReader doesn't care about the rest.)
CSV_HEADER = "game_id,season,game_type,week,gameday,gametime,away_team,home_team"


def _csv_row(
    season: int,
    week: int,
    away: str,
    home: str,
    *,
    game_type: str = "REG",
    gameday: str = "2026-09-13",
    gametime: str = "13:00",
) -> str:
    game_id = f"{season}_{week:02d}_{away}_{home}"
    return ",".join(
        [game_id, str(season), game_type, str(week), gameday, gametime, away, home]
    )


def _csv(*rows: str) -> str:
    return "\n".join((CSV_HEADER, *rows))


def _round_robin(season: int, teams: list[str], weeks: int) -> list[str]:
    """One game per pair of consecutive teams, per week -- every team plays
    every listed week, so nobody has a bye unless a week is left out."""
    rows = []
    for week in range(1, weeks + 1):
        for i in range(0, len(teams) - 1, 2):
            rows.append(_csv_row(season, week, teams[i], teams[i + 1]))
    return rows


@pytest.fixture(autouse=True)
def no_real_download(monkeypatch):
    def _boom():
        raise AssertionError("nfl_schedule._fetch_csv_text was not monkeypatched")

    monkeypatch.setattr(nfl_schedule, "_fetch_csv_text", _boom)


@pytest.fixture()
def season(monkeypatch) -> int:
    """Pin the "current" season so tests don't drift with the calendar."""
    monkeypatch.setattr(nfl_schedule, "current_nfl_season", lambda: 2026)
    return 2026


def stub_csv(monkeypatch, text: str) -> None:
    monkeypatch.setattr(nfl_schedule, "_fetch_csv_text", lambda: text)


def add_game(db, season: int, week: int, home: str, away: str) -> NflGame:
    game = NflGame(season=season, week=week, home_team=home, away_team=away)
    db.add(game)
    db.flush()
    return game


# --- normalize_team --------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # The schedule CSV's own spellings.
        ("LA", "LAR"),
        ("LAC", "LAC"),
        ("JAX", "JAX"),
        ("WAS", "WAS"),
        ("GB", "GB"),
        # MFL-style spellings the nflverse crosswalk writes onto Player rows.
        ("GBP", "GB"),
        ("KCC", "KC"),
        ("NEP", "NE"),
        ("NOS", "NO"),
        ("SFO", "SF"),
        ("TBB", "TB"),
        ("LVR", "LV"),
        ("SDC", "LAC"),
        ("RAM", "LAR"),
        # Already handled by matching.normalize_team_abbr.
        ("JAC", "JAX"),
        ("WSH", "WAS"),
        ("OAK", "LV"),
        ("STL", "LAR"),
        # Junk passes through rather than being guessed at.
        ("FA", "FA"),
        (None, ""),
        ("", ""),
    ],
)
def test_normalize_team(raw, expected):
    assert nfl_schedule.normalize_team(raw) == expected


# --- parsing / upsert ------------------------------------------------------


def test_refresh_schedule_loads_current_season_regular_games(
    db_session, monkeypatch, season
):
    stub_csv(
        monkeypatch,
        _csv(
            _csv_row(season, 1, "NE", "SEA", gameday="2026-09-09", gametime="20:20"),
            _csv_row(season, 2, "SEA", "NE"),
            # Filtered out: wrong season, and a postseason game.
            _csv_row(season - 1, 1, "NE", "SEA"),
            _csv_row(season, 20, "NE", "SEA", game_type="POST"),
        ),
    )

    message = nfl_schedule.refresh_schedule(db_session)

    games = db_session.query(NflGame).order_by(NflGame.week).all()
    assert [(g.season, g.week, g.away_team, g.home_team) for g in games] == [
        (season, 1, "NE", "SEA"),
        (season, 2, "SEA", "NE"),
    ]
    assert games[0].kickoff.isoformat() == "2026-09-09T20:20:00"
    assert "2 games" in message and "2 created" in message


def test_refresh_schedule_normalizes_team_abbreviations(
    db_session, monkeypatch, season
):
    stub_csv(monkeypatch, _csv(_csv_row(season, 1, "LA", "JAC")))

    nfl_schedule.refresh_schedule(db_session)

    game = db_session.query(NflGame).one()
    assert (game.away_team, game.home_team) == ("LAR", "JAX")


def test_refresh_schedule_upserts_rather_than_duplicating(
    db_session, monkeypatch, season
):
    stub_csv(monkeypatch, _csv(_csv_row(season, 1, "NE", "SEA")))
    nfl_schedule.refresh_schedule(db_session)

    # Same season/week/home, different opponent and kickoff: the flex-schedule
    # case. It must update the existing row, not add a second one.
    stub_csv(
        monkeypatch,
        _csv(_csv_row(season, 1, "SF", "SEA", gameday="2026-09-14", gametime="20:15")),
    )
    message = nfl_schedule.refresh_schedule(db_session)

    game = db_session.query(NflGame).one()
    assert game.away_team == "SF"
    assert game.kickoff.isoformat() == "2026-09-14T20:15:00"
    assert "0 created, 1 updated" in message


def test_refresh_schedule_handles_missing_gametime(db_session, monkeypatch, season):
    stub_csv(
        monkeypatch,
        _csv(_csv_row(season, 1, "NE", "SEA", gameday="2026-09-13", gametime="")),
    )

    nfl_schedule.refresh_schedule(db_session)

    game = db_session.query(NflGame).one()
    assert game.kickoff.isoformat() == "2026-09-13T00:00:00"


def test_refresh_schedule_writes_sync_log(db_session, monkeypatch, season):
    stub_csv(monkeypatch, _csv(_csv_row(season, 1, "NE", "SEA")))

    nfl_schedule.refresh_schedule(db_session)

    log = db_session.query(SyncLog).filter(SyncLog.resource == "nfl_schedule").one()
    assert log.status == "success"
    assert log.finished_at is not None


def test_refresh_schedule_logs_the_error_and_reraises(db_session, monkeypatch, season):
    def boom():
        raise RuntimeError("nflverse is down")

    monkeypatch.setattr(nfl_schedule, "_fetch_csv_text", boom)

    with pytest.raises(RuntimeError):
        nfl_schedule.refresh_schedule(db_session)

    log = db_session.query(SyncLog).filter(SyncLog.resource == "nfl_schedule").one()
    assert log.status == "error"
    assert "nflverse is down" in log.message


# --- bye weeks -------------------------------------------------------------


def test_bye_weeks_is_the_week_with_no_game(db_session, season):
    # SEA/NE play weeks 1-18 except week 7; SF/LAR except week 11.
    for week in range(1, 19):
        if week != 7:
            add_game(db_session, season, week, "SEA", "NE")
        if week != 11:
            add_game(db_session, season, week, "SF", "LAR")
    db_session.commit()

    assert nfl_schedule.bye_weeks(db_session, season) == {
        "SEA": 7,
        "NE": 7,
        "SF": 11,
        "LAR": 11,
    }


def test_bye_weeks_takes_the_first_of_several_missing_weeks(
    db_session, season, caplog
):
    for week in (1, 2, 3):
        add_game(db_session, season, week, "SEA", "NE")
    db_session.commit()

    with caplog.at_level(logging.WARNING, logger=nfl_schedule.__name__):
        byes = nfl_schedule.bye_weeks(db_session, season)

    assert byes == {"SEA": 4, "NE": 4}
    assert any("missing 15 weeks" in record.getMessage() for record in caplog.records)


def test_bye_weeks_skips_a_team_with_no_missing_week(db_session, season, caplog):
    for week in range(1, 19):
        add_game(db_session, season, week, "SEA", "NE")
    db_session.commit()

    with caplog.at_level(logging.WARNING, logger=nfl_schedule.__name__):
        byes = nfl_schedule.bye_weeks(db_session, season)

    assert byes == {}
    assert any("no bye recorded" in record.getMessage() for record in caplog.records)


def test_refresh_schedule_recomputes_player_bye_weeks(db_session, monkeypatch, season):
    teams = ["SEA", "NE", "SF", "LAR"]
    rows = _round_robin(season, teams, 18)
    # Drop SEA/NE's week-7 game and SF/LAR's week-11 game to create the byes.
    rows = [
        row
        for row in rows
        if row not in (_csv_row(season, 7, "SEA", "NE"), _csv_row(season, 11, "SF", "LAR"))
    ]
    stub_csv(monkeypatch, _csv(*rows))

    seahawk = Player(full_name="Sea WR", position="WR", nfl_team="SEA")
    ram = Player(full_name="Ram RB", position="RB", nfl_team="LAR")
    # Crosswalk-spelled team, and a crosswalk-spelled position.
    ram_kicker = Player(full_name="Ram K", position="PK", nfl_team="RAM")
    defense = Player(full_name="Seattle Seahawks", position="DEF", nfl_team="SEA")
    unknown = Player(full_name="Free Agent", position="WR", nfl_team="FA")
    teamless = Player(full_name="No Team", position="WR", nfl_team=None)
    # Non-fantasy positions are left alone entirely.
    lineman = Player(full_name="Big Guy", position="OT", nfl_team="SEA", bye_week=99)
    db_session.add_all(
        [seahawk, ram, ram_kicker, defense, unknown, teamless, lineman]
    )
    db_session.commit()

    nfl_schedule.refresh_schedule(db_session)

    assert seahawk.bye_week == 7
    assert defense.bye_week == 7
    assert ram.bye_week == 11
    assert ram_kicker.bye_week == 11
    assert unknown.bye_week is None
    assert teamless.bye_week is None
    assert lineman.bye_week == 99


def test_refresh_bye_weeks_clears_a_stale_value(db_session, season):
    for week in range(1, 19):
        if week != 7:
            add_game(db_session, season, week, "SEA", "NE")
    # This player was traded away from a team with a week-7 bye.
    player = Player(full_name="Traded WR", position="WR", nfl_team="DAL", bye_week=7)
    db_session.add(player)
    db_session.commit()

    changed = nfl_schedule.refresh_bye_weeks(db_session, season)

    assert player.bye_week is None
    assert changed == 1


def test_refresh_bye_weeks_is_a_noop_with_no_schedule(db_session, season):
    player = Player(full_name="WR", position="WR", nfl_team="SEA", bye_week=5)
    db_session.add(player)
    db_session.commit()

    assert nfl_schedule.refresh_bye_weeks(db_session, season) == 0
    assert player.bye_week == 5


# --- team_remaining_games --------------------------------------------------


def test_team_remaining_games_counts_home_and_away(db_session, season):
    add_game(db_session, season, 1, "SEA", "NE")
    add_game(db_session, season, 2, "NE", "SEA")
    add_game(db_session, season, 3, "SEA", "SF")
    db_session.commit()

    assert nfl_schedule.team_remaining_games(db_session, season, 1) == {
        "SEA": 3,
        "NE": 2,
        "SF": 1,
    }


def test_team_remaining_games_respects_from_week(db_session, season):
    add_game(db_session, season, 1, "SEA", "NE")
    add_game(db_session, season, 2, "NE", "SEA")
    add_game(db_session, season, 3, "SEA", "SF")
    db_session.commit()

    assert nfl_schedule.team_remaining_games(db_session, season, 3) == {
        "SEA": 1,
        "SF": 1,
    }


def test_team_remaining_games_ignores_other_seasons(db_session, season):
    add_game(db_session, season - 1, 1, "SEA", "NE")
    db_session.commit()

    assert nfl_schedule.team_remaining_games(db_session, season, 1) == {}


# --- team_opponent ---------------------------------------------------------


def test_team_opponent_labels_home_and_away(db_session, season):
    add_game(db_session, season, 5, home="SEA", away="NE")
    db_session.commit()

    assert nfl_schedule.team_opponent(db_session, season, 5) == {
        "SEA": "vs NE",
        "NE": "@ SEA",
    }


def test_team_opponent_omits_a_team_on_bye(db_session, season):
    add_game(db_session, season, 5, home="SEA", away="NE")
    add_game(db_session, season, 6, home="SF", away="LAR")
    db_session.commit()

    week_six = nfl_schedule.team_opponent(db_session, season, 6)
    assert "SEA" not in week_six
    assert week_six == {"SF": "vs LAR", "LAR": "@ SF"}


def test_team_opponent_with_no_week(db_session, season):
    add_game(db_session, season, 5, home="SEA", away="NE")
    db_session.commit()

    assert nfl_schedule.team_opponent(db_session, season, None) == {}
