"""Actual weekly stat ingestion: column mapping, matching, upsert, wiring.

No network: ``_fetch_csv_text`` is monkeypatched everywhere and the autouse
fixture below makes a test that forgets to do so fail loudly rather than
downloading ~9MB from nflverse. The CSV fixtures use the *real* column names,
verified against the live 2025 file (see ``app.services.actuals``' docstring).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models import ActualStat, Player, SyncLog
from app.services import actuals

SEASON = 2026

#: The real (unpatched) fetch, captured at import time so the tests that want
#: it can put it back after the autouse fixture swaps in its landmine.
_real_fetch = actuals._fetch_csv_text

#: The columns this ingester actually reads, using the live file's exact
#: spellings. (The real file carries 150 columns; DictReader ignores the rest.)
CSV_COLUMNS = (
    "player_id",
    "player_name",
    "player_display_name",
    "position",
    "position_group",
    "season",
    "week",
    "season_type",
    "team",
    "opponent_team",
    "completions",
    "attempts",
    "passing_yards",
    "passing_tds",
    "passing_interceptions",
    "sacks_suffered",
    "sack_fumbles_lost",
    "passing_2pt_conversions",
    "carries",
    "rushing_yards",
    "rushing_tds",
    "rushing_fumbles_lost",
    "rushing_2pt_conversions",
    "receptions",
    "targets",
    "receiving_yards",
    "receiving_tds",
    "receiving_fumbles_lost",
    "receiving_2pt_conversions",
    "special_teams_tds",
    "fumbles_total",
    "fumbles_lost_total",
    "punt_return_yards",
    "kickoff_return_yards",
    "fg_made",
    "fg_att",
    "fg_missed",
    "fg_blocked",
    "fg_made_0_19",
    "fg_made_20_29",
    "fg_made_30_39",
    "fg_made_40_49",
    "fg_made_50_59",
    "fg_made_60_",
    "pat_made",
    "pat_att",
    "pat_missed",
    "pat_blocked",
    "fantasy_points",
    "fantasy_points_ppr",
)


def csv_row(**values) -> str:
    unknown = set(values) - set(CSV_COLUMNS)
    assert not unknown, f"not a real nflverse column: {sorted(unknown)}"
    defaults = {
        "season": SEASON,
        "season_type": "REG",
        "week": 1,
        "team": "SF",
        "opponent_team": "SEA",
    }
    row = {**{column: "0" for column in CSV_COLUMNS}, **defaults, **values}
    for text_column in ("player_id", "player_name", "player_display_name", "position"):
        row.setdefault(text_column, "")
    return ",".join(str(row[column]) for column in CSV_COLUMNS)


def csv_text(*rows: str) -> str:
    return "\n".join((",".join(CSV_COLUMNS), *rows))


def stub_csv(monkeypatch, text: str) -> None:
    monkeypatch.setattr(actuals, "_fetch_csv_text", lambda season: text)


@pytest.fixture(autouse=True)
def no_real_download(monkeypatch):
    def _boom(season):
        raise AssertionError("actuals._fetch_csv_text was not monkeypatched")

    monkeypatch.setattr(actuals, "_fetch_csv_text", _boom)
    monkeypatch.setattr(actuals, "current_nfl_season", lambda: SEASON)


def make_player(db, name: str, position: str, *, gsis_id: str | None = None) -> Player:
    player = Player(full_name=name, position=position, nfl_team="SF", gsis_id=gsis_id)
    db.add(player)
    db.commit()
    return player


# --- column mapping --------------------------------------------------------


def test_translate_maps_every_verified_offensive_column():
    row = dict(
        zip(
            CSV_COLUMNS,
            csv_row(
                completions=24,
                attempts=36,
                passing_yards=305,
                passing_tds=3,
                passing_interceptions=1,
                sacks_suffered=2,
                passing_2pt_conversions=1,
                carries=5,
                rushing_yards=31,
                rushing_tds=1,
                rushing_2pt_conversions=1,
                receptions=1,
                receiving_yards=9,
                receiving_tds=1,
                receiving_2pt_conversions=1,
                fumbles_lost_total=1,
                special_teams_tds=1,
                punt_return_yards=12,
                kickoff_return_yards=20,
            ).split(","),
        )
    )

    assert actuals.translate_stat_line(row) == {
        "pass_cmp": 24.0,
        "pass_att": 36.0,
        "pass_yds": 305.0,
        "pass_td": 3.0,
        "pass_int": 1.0,
        "pass_sacked": 2.0,
        "pass_2pt": 1.0,
        "rush_att": 5.0,
        "rush_yds": 31.0,
        "rush_td": 1.0,
        "rush_2pt": 1.0,
        "rec": 1.0,
        "rec_yds": 9.0,
        "rec_td": 1.0,
        "rec_2pt": 1.0,
        "fum_lost": 1.0,
        "ret_td": 1.0,
        "ret_yds": 32.0,  # punt + kickoff return yards
    }


def test_translate_maps_kicking_and_never_double_counts_fg_made():
    """The distance buckets are stored; the ``fg_made`` total deliberately is not.

    ``scoring._STANDARD`` pays for ``fg_made`` *and* for each bucket, so
    keeping both would score every kick twice.
    """
    row = dict(
        zip(
            CSV_COLUMNS,
            csv_row(
                position="K",
                fg_made=4,
                fg_att=6,
                fg_missed=1,
                fg_blocked=1,
                fg_made_20_29=1,
                fg_made_30_39=1,
                fg_made_50_59=1,
                fg_made_60_=1,
                pat_made=3,
                pat_att=4,
                pat_missed=1,
            ).split(","),
        )
    )

    line = actuals.translate_stat_line(row)

    assert "fg_made" not in line
    assert line == {
        "fg_20_29": 1.0,
        "fg_30_39": 1.0,
        "fg_50_plus": 2.0,  # 50-59 and 60+ collapse into one bucket
        "fg_miss": 2.0,  # missed + blocked
        "xp_made": 3.0,
        "xp_miss": 1.0,
    }


def test_translate_takes_fumbles_from_the_combined_column():
    """A return fumble shows up only in ``fumbles_lost_total``.

    Verified on the live 2025 file: 39 of the 253 rows with a lost fumble have
    all three component columns at zero.
    """
    row = dict(
        zip(
            CSV_COLUMNS,
            csv_row(
                receptions=2,
                receiving_yards=20,
                fumbles_total=1,
                fumbles_lost_total=1,
                sack_fumbles_lost=0,
                rushing_fumbles_lost=0,
                receiving_fumbles_lost=0,
            ).split(","),
        )
    )

    assert actuals.translate_stat_line(row)["fum_lost"] == 1.0


def test_translate_ignores_source_computed_fantasy_points():
    row = dict(
        zip(
            CSV_COLUMNS,
            csv_row(receptions=3, receiving_yards=30, fantasy_points=6, fantasy_points_ppr=9).split(","),
        )
    )

    line = actuals.translate_stat_line(row)

    assert "fantasy_points" not in line
    assert line == {"rec": 3.0, "rec_yds": 30.0}


# --- ingestion -------------------------------------------------------------


def test_refresh_matches_by_gsis_id(db_session, monkeypatch):
    player = make_player(db_session, "Actual Guy", "WR", gsis_id="00-0011111")
    stub_csv(
        monkeypatch,
        csv_text(
            csv_row(
                player_id="00-0011111",
                player_display_name="Totally Different Spelling",
                position="WR",
                week=2,
                receptions=6,
                receiving_yards=71,
                receiving_tds=1,
            )
        ),
    )

    result = actuals.refresh_actuals(db_session)

    assert (result["matched_by_gsis"], result["matched_by_name"]) == (1, 0)
    assert result["weeks"] == [2]
    row = db_session.query(ActualStat).one()
    assert (row.player_id, row.season, row.week) == (player.id, SEASON, 2)
    assert row.stat_json == {"rec": 6.0, "rec_yds": 71.0, "rec_td": 1.0}


def test_refresh_falls_back_to_name_position_and_backfills_gsis(db_session, monkeypatch):
    player = make_player(db_session, "Michael Pittman Jr.", "WR")
    stub_csv(
        monkeypatch,
        csv_text(
            csv_row(
                player_id="00-0022222",
                player_display_name="Michael Pittman",
                position="WR",
                receptions=4,
                receiving_yards=44,
            )
        ),
    )

    result = actuals.refresh_actuals(db_session)

    assert (result["matched_by_gsis"], result["matched_by_name"]) == (0, 1)
    db_session.refresh(player)
    assert player.gsis_id == "00-0022222"
    assert db_session.query(ActualStat).one().player_id == player.id


def test_refresh_counts_unmatched_and_ambiguous_players(db_session, monkeypatch):
    make_player(db_session, "Josh Allen", "WR")
    make_player(db_session, "Josh Allen", "WR")  # ambiguous: two same-name WRs
    stub_csv(
        monkeypatch,
        csv_text(
            csv_row(
                player_id="00-0033333",
                player_display_name="Josh Allen",
                position="WR",
                receptions=3,
                receiving_yards=30,
            ),
            csv_row(
                player_id="00-0044444",
                player_display_name="Nobody We Know",
                position="RB",
                carries=9,
                rushing_yards=40,
            ),
        ),
    )

    result = actuals.refresh_actuals(db_session)

    assert result["unmatched"] == 2
    assert result["saved"] == 0
    assert db_session.query(ActualStat).count() == 0


def test_refresh_skips_postseason_other_seasons_and_non_offense(db_session, monkeypatch):
    make_player(db_session, "Reg Guy", "RB", gsis_id="00-0055555")
    make_player(db_session, "Line Backer", "LB", gsis_id="00-0066666")
    stub_csv(
        monkeypatch,
        csv_text(
            csv_row(player_id="00-0055555", position="RB", week=1, rushing_yards=50),
            csv_row(
                player_id="00-0055555",
                position="RB",
                week=20,
                season_type="POST",
                rushing_yards=99,
            ),
            csv_row(
                player_id="00-0055555",
                position="RB",
                season=SEASON - 1,
                week=3,
                rushing_yards=77,
            ),
            # A defender: real column values, but no fantasy line we grade.
            csv_row(
                player_id="00-0066666",
                player_display_name="Line Backer",
                position="LB",
                special_teams_tds=1,
            ),
            # An offensive row with nothing on it at all.
            csv_row(player_id="00-0055555", position="RB", week=4),
        ),
    )

    result = actuals.refresh_actuals(db_session)

    assert result["weeks"] == [1]
    stored = db_session.query(ActualStat).all()
    assert [(row.week, row.stat_json) for row in stored] == [(1, {"rush_yds": 50.0})]


def test_refresh_is_idempotent_and_updates_corrected_stats(db_session, monkeypatch):
    make_player(db_session, "Corrected Guy", "RB", gsis_id="00-0077777")
    stub_csv(
        monkeypatch,
        csv_text(csv_row(player_id="00-0077777", position="RB", rushing_yards=50)),
    )

    first = actuals.refresh_actuals(db_session)
    second = actuals.refresh_actuals(db_session)

    assert (first["created"], first["updated"]) == (1, 0)
    assert (second["created"], second["updated"]) == (0, 0)
    assert db_session.query(ActualStat).count() == 1

    # nflverse folds in a stat correction a few days later.
    stub_csv(
        monkeypatch,
        csv_text(
            csv_row(
                player_id="00-0077777", position="RB", rushing_yards=62, rushing_tds=1
            )
        ),
    )
    third = actuals.refresh_actuals(db_session)

    assert (third["created"], third["updated"]) == (0, 1)
    assert db_session.query(ActualStat).count() == 1
    assert db_session.query(ActualStat).one().stat_json == {
        "rush_yds": 62.0,
        "rush_td": 1.0,
    }


def test_refresh_writes_a_sync_log(db_session, monkeypatch):
    make_player(db_session, "Logged Guy", "RB", gsis_id="00-0088888")
    stub_csv(
        monkeypatch,
        csv_text(csv_row(player_id="00-0088888", position="RB", rushing_yards=50)),
    )

    actuals.refresh_actuals(db_session)

    log = db_session.query(SyncLog).filter(SyncLog.resource == "nfl_actuals").one()
    assert log.status == "success"
    assert "1 player-week actual(s)" in log.message


def test_refresh_treats_a_missing_season_file_as_an_empty_sync(db_session, monkeypatch, tmp_path):
    """nflverse cuts no file until the season's first games are played (404)."""

    class FakeResponse:
        status_code = 404

        def raise_for_status(self):  # pragma: no cover - never reached on a 404
            raise AssertionError("raise_for_status called on a handled 404")

    monkeypatch.setattr(actuals, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(actuals.httpx, "get", lambda *args, **kwargs: FakeResponse())
    # Put the real fetch back (the autouse fixture replaced it with a landmine)
    # so the 404 travels the whole download -> cache -> refresh path.
    monkeypatch.setattr(actuals, "_fetch_csv_text", _real_fetch)

    result = actuals.refresh_actuals(db_session)

    assert result == {
        "season": SEASON,
        "available": False,
        "weeks": [],
        "saved": 0,
        "created": 0,
        "updated": 0,
        "matched_by_gsis": 0,
        "matched_by_name": 0,
        "unmatched": 0,
    }
    log = db_session.query(SyncLog).filter(SyncLog.resource == "nfl_actuals").one()
    assert log.status == "success"
    assert "no weekly player stats published yet" in log.message
    assert db_session.query(ActualStat).count() == 0


def test_cached_csv_is_reused_without_downloading(monkeypatch, tmp_path):
    calls = []

    def fake_download(season):
        calls.append(season)
        return csv_text(csv_row(player_id="00-0099999", position="RB", rushing_yards=5))

    monkeypatch.setattr(actuals, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(actuals, "_download_csv", fake_download)
    monkeypatch.setattr(actuals, "_fetch_csv_text", _real_fetch)

    first = actuals._fetch_csv_text(SEASON)
    second = actuals._fetch_csv_text(SEASON)

    assert first == second
    assert calls == [SEASON]  # second call came from data/cache
    assert (tmp_path / f"nflverse_stats_player_week_{SEASON}.csv").exists()


def test_actual_weeks_lists_weeks_on_file(db_session):
    player = make_player(db_session, "Weeks Guy", "RB")
    other = make_player(db_session, "Other Guy", "WR")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for owner, week in ((player, 3), (player, 1), (other, 3), (player, 9)):
        db_session.add(
            ActualStat(
                player_id=owner.id,
                season=SEASON,
                week=week,
                stat_json={"rush_yds": 10.0},
                fetched_at=now,
            )
        )
    db_session.commit()

    assert actuals.actual_weeks(db_session, SEASON) == [1, 3, 9]
    assert actuals.actual_weeks(db_session, SEASON - 1) == []


# --- wiring ----------------------------------------------------------------


def test_registry_entry_is_wired():
    from app.routers.data import REFRESH_REGISTRY

    assert REFRESH_REGISTRY["nfl_actuals"] == ("app.services.actuals", "refresh_actuals")


def test_scheduler_runs_actuals_tuesday_and_friday(monkeypatch):
    import app.services.scheduler as scheduler_module
    from apscheduler.schedulers.background import BackgroundScheduler

    from app.config import settings

    fresh = BackgroundScheduler()
    monkeypatch.setattr(scheduler_module, "scheduler", fresh)
    monkeypatch.setattr(scheduler_module, "_started", False)
    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", True)
    try:
        scheduler_module.start_scheduler()
        job = fresh.get_job(scheduler_module._job_id("nfl_actuals"))
        assert job is not None
        fields = {field.name: str(field) for field in job.trigger.fields}
        assert fields["day_of_week"] == "tue,fri"
        assert (fields["hour"], fields["minute"]) == ("4", "0")
    finally:
        if fresh.running:
            fresh.shutdown(wait=False)
