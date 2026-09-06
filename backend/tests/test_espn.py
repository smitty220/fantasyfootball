"""ESPN service tests. All HTTP is mocked via respx; nothing leaves the machine."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.models import Player, Projection, SyncLog
from app.services import espn

SEASON = 2026


@pytest.fixture(autouse=True)
def no_paging_delay(monkeypatch):
    monkeypatch.setattr(espn, "PAGE_DELAY_SECONDS", 0)


def _espn_url() -> str:
    return espn.BASE_URL.format(season=SEASON)


def _player_entry(
    espn_id: int,
    full_name: str,
    default_position_id: int,
    stats: list[dict],
    pro_team_id: int | None = None,
) -> dict:
    player: dict = {
        "id": espn_id,
        "fullName": full_name,
        "defaultPositionId": default_position_id,
        "stats": stats,
    }
    if pro_team_id is not None:
        player["proTeamId"] = pro_team_id
    return {"player": player}


def _season_stat_entry(stats: dict, season: int = SEASON) -> dict:
    return {
        "statSourceId": 1,
        "statSplitTypeId": 0,
        "scoringPeriodId": 0,
        "seasonId": season,
        "stats": stats,
    }


def _week_stat_entry(week: int, stats: dict, season: int = SEASON) -> dict:
    return {
        "statSourceId": 1,
        "statSplitTypeId": 1,
        "scoringPeriodId": week,
        "seasonId": season,
        "stats": stats,
    }


def _actual_stat_entry(stats: dict, season: int = SEASON) -> dict:
    return {
        "statSourceId": 0,
        "statSplitTypeId": 0,
        "scoringPeriodId": 0,
        "seasonId": season,
        "stats": stats,
    }


# --------------------------------------------------------------------------- #
# stat-ID translation
# --------------------------------------------------------------------------- #


def test_translate_stat_line_maps_known_ids_to_canonical():
    raw = {
        "0": 500.0,  # pass_att
        "1": 320.0,  # pass_cmp
        "3": 4000.0,  # pass_yds
        "4": 28.0,  # pass_td
        "20": 10.0,  # pass_int
        "23": 50.0,  # rush_att (also present for a mobile QB)
        "24": 300.0,  # rush_yds
        "999": 12345.0,  # unknown id -- must be dropped
    }
    result = espn.translate_stat_line(raw)
    assert result == {
        "pass_att": 500.0,
        "pass_cmp": 320.0,
        "pass_yds": 4000.0,
        "pass_td": 28.0,
        "pass_int": 10.0,
        "rush_att": 50.0,
        "rush_yds": 300.0,
    }


def test_translate_stat_line_receptions_prefers_season_key_then_week_key():
    # Season-level aggregates key receptions under "53"; per-game rows use "41".
    assert espn.translate_stat_line({"53": 80.0, "42": 900.0})["rec"] == 80.0
    assert espn.translate_stat_line({"41": 5.0, "42": 60.0})["rec"] == 5.0


def test_translate_stat_line_kicking_and_dst():
    kicking = espn.translate_stat_line(
        {"74": 5.0, "77": 8.0, "80": 15.0, "85": 3.0, "86": 30.0, "88": 1.0}
    )
    assert kicking == {
        "fg_50_plus": 5.0,
        "fg_40_49": 8.0,
        "fg_made": 15.0,
        "fg_miss": 3.0,
        "xp_made": 30.0,
        "xp_miss": 1.0,
    }

    dst = espn.translate_stat_line(
        {
            "95": 14.0,
            "96": 8.0,
            "97": 2.0,
            "98": 1.0,
            "99": 40.0,
            "105": 3.0,
            "120": 300.0,
            "127": 5000.0,
        }
    )
    assert dst == {
        "dst_int": 14.0,
        "dst_fum_rec": 8.0,
        "dst_blk": 2.0,
        "dst_safety": 1.0,
        "dst_sack": 40.0,
        "dst_td": 3.0,
        "dst_pts_allowed": 300.0,
        "dst_yds_allowed": 5000.0,
    }


# --------------------------------------------------------------------------- #
# week vs. season rows
# --------------------------------------------------------------------------- #


@respx.mock
def test_refresh_projections_season_row(db_session):
    player = Player(full_name="Josh Allen", position="QB", espn_id="3918298")
    db_session.add(player)
    db_session.commit()

    payload = {
        "players": [
            _player_entry(
                3918298,
                "Josh Allen",
                1,
                [
                    _actual_stat_entry({"3": 1.0}),
                    _week_stat_entry(5, {"3": 250.0, "4": 2.0}),
                    _season_stat_entry({"3": 4000.0, "4": 30.0, "20": 9.0}),
                ],
            )
        ]
    }
    respx.get(_espn_url()).mock(
        side_effect=[httpx.Response(200, json=payload), httpx.Response(200, json={"players": []})]
    )

    result = espn.refresh_projections(db_session, SEASON, week=None)
    assert result["saved"] == 1

    row = (
        db_session.query(Projection)
        .filter(Projection.source == "espn", Projection.week.is_(None))
        .one()
    )
    assert row.stat_json == {"pass_yds": 4000.0, "pass_td": 30.0, "pass_int": 9.0}
    assert row.player_id == player.id


@respx.mock
def test_refresh_projections_week_row(db_session):
    player = Player(full_name="Josh Allen", position="QB", espn_id="3918298")
    db_session.add(player)
    db_session.commit()

    payload = {
        "players": [
            _player_entry(
                3918298,
                "Josh Allen",
                1,
                [
                    _week_stat_entry(5, {"3": 250.0, "4": 2.0}),
                    _season_stat_entry({"3": 4000.0, "4": 30.0}),
                ],
            )
        ]
    }
    respx.get(_espn_url()).mock(
        side_effect=[httpx.Response(200, json=payload), httpx.Response(200, json={"players": []})]
    )

    result = espn.refresh_projections(db_session, SEASON, week=5)
    assert result["saved"] == 1

    row = (
        db_session.query(Projection)
        .filter(Projection.source == "espn", Projection.week == 5)
        .one()
    )
    assert row.stat_json == {"pass_yds": 250.0, "pass_td": 2.0}


# --------------------------------------------------------------------------- #
# matching
# --------------------------------------------------------------------------- #


@respx.mock
def test_refresh_projections_matches_by_espn_id(db_session):
    player = Player(full_name="Some Guy", position="QB", espn_id="111")
    db_session.add(player)
    db_session.commit()

    payload = {
        "players": [
            _player_entry(111, "A Totally Different Name", 1, [_season_stat_entry({"3": 3000.0})])
        ]
    }
    respx.get(_espn_url()).mock(
        side_effect=[httpx.Response(200, json=payload), httpx.Response(200, json={"players": []})]
    )

    result = espn.refresh_projections(db_session, SEASON, week=None)
    assert result["matched_by_id"] == 1
    assert result["matched_by_name"] == 0
    assert db_session.query(Player).count() == 1

    row = db_session.query(Projection).one()
    assert row.player_id == player.id


@respx.mock
def test_refresh_projections_matches_by_name_and_backfills_espn_id(db_session):
    player = Player(full_name="Justin Jefferson", position="WR", espn_id=None)
    db_session.add(player)
    db_session.commit()

    payload = {
        "players": [
            _player_entry(4262921, "Justin Jefferson", 3, [_season_stat_entry({"42": 1400.0})])
        ]
    }
    respx.get(_espn_url()).mock(
        side_effect=[httpx.Response(200, json=payload), httpx.Response(200, json={"players": []})]
    )

    result = espn.refresh_projections(db_session, SEASON, week=None)
    assert result["matched_by_name"] == 1
    assert result["matched_by_id"] == 0
    assert db_session.query(Player).count() == 1

    db_session.refresh(player)
    assert player.espn_id == "4262921"


@respx.mock
def test_refresh_projections_matches_dst_by_position_and_team(db_session):
    # A Sleeper-style DEF row: full_name "Houston Texans", sleeper_id "HOU",
    # no espn_id -- exactly what app.services.sleeper.refresh_players creates
    # for team defenses (see that module's docstring).
    texans_dst = Player(
        full_name="Houston Texans",
        position="DEF",
        nfl_team="HOU",
        sleeper_id="HOU",
        espn_id=None,
    )
    db_session.add(texans_dst)
    db_session.commit()

    payload = {
        "players": [
            _player_entry(
                -16034,
                "Texans D/ST",
                16,
                [_season_stat_entry({"120": 300.0, "127": 5000.0})],
                pro_team_id=34,
            )
        ]
    }
    respx.get(_espn_url()).mock(
        side_effect=[httpx.Response(200, json=payload), httpx.Response(200, json={"players": []})]
    )

    result = espn.refresh_projections(db_session, SEASON, week=None)
    assert result["matched_by_id"] == 0
    assert result["unmatched"] == 0
    assert result["saved"] == 1
    assert db_session.query(Player).count() == 1

    row = db_session.query(Projection).one()
    assert row.player_id == texans_dst.id
    assert row.stat_json == {"dst_pts_allowed": 300.0, "dst_yds_allowed": 5000.0}

    db_session.refresh(texans_dst)
    assert texans_dst.espn_id == "-16034"


@respx.mock
def test_refresh_projections_unmatched_player_is_skipped(db_session):
    payload = {
        "players": [
            _player_entry(999999, "Nobody Anyone Knows", 1, [_season_stat_entry({"3": 1000.0})])
        ]
    }
    respx.get(_espn_url()).mock(
        side_effect=[httpx.Response(200, json=payload), httpx.Response(200, json={"players": []})]
    )

    result = espn.refresh_projections(db_session, SEASON, week=None)
    assert result["unmatched"] == 1
    assert result["saved"] == 0
    assert db_session.query(Player).count() == 0
    assert db_session.query(Projection).count() == 0


# --------------------------------------------------------------------------- #
# upsert idempotency + sync log
# --------------------------------------------------------------------------- #


@respx.mock
def test_refresh_projections_upsert_idempotent(db_session):
    player = Player(full_name="Josh Allen", position="QB", espn_id="3918298")
    db_session.add(player)
    db_session.commit()

    def make_payload(pass_yds: float) -> dict:
        return {
            "players": [
                _player_entry(
                    3918298, "Josh Allen", 1, [_season_stat_entry({"3": pass_yds, "4": 30.0})]
                )
            ]
        }

    respx.get(_espn_url()).mock(
        side_effect=[
            httpx.Response(200, json=make_payload(4000.0)),
            httpx.Response(200, json={"players": []}),
        ]
    )
    espn.refresh_projections(db_session, SEASON, week=None)

    respx.routes.clear()
    respx.get(_espn_url()).mock(
        side_effect=[
            httpx.Response(200, json=make_payload(4200.0)),
            httpx.Response(200, json={"players": []}),
        ]
    )
    espn.refresh_projections(db_session, SEASON, week=None)

    rows = db_session.query(Projection).filter(Projection.source == "espn").all()
    assert len(rows) == 1
    assert rows[0].stat_json["pass_yds"] == 4200.0

    log = db_session.query(SyncLog).filter(SyncLog.resource == "espn_projections").all()
    assert len(log) == 2
    assert all(entry.status == "success" for entry in log)


# --------------------------------------------------------------------------- #
# current week + weekly refresh
# --------------------------------------------------------------------------- #


@pytest.fixture()
def season_2026(monkeypatch):
    """Pin the "current" season so weekly tests don't drift with the calendar."""
    monkeypatch.setattr(
        "app.services.yahoo.sync.current_nfl_season", lambda *a, **k: SEASON
    )


def _league_document(scoring_period_id) -> dict:
    """The bare leaguedefaults document, shaped like the live one."""
    return {
        "gameId": 1,
        "id": 3,
        "scoringPeriodId": scoring_period_id,
        "seasonId": SEASON,
        "segmentId": 0,
        "status": {"currentMatchupPeriod": 1, "latestScoringPeriod": 0},
    }


@respx.mock
def test_current_week_reads_scoring_period_id(db_session):
    respx.get(_espn_url()).mock(
        return_value=httpx.Response(200, json=_league_document(4))
    )
    assert espn.current_week(SEASON) == 4


@respx.mock
@pytest.mark.parametrize("value", [None, 0, "nope"])
def test_current_week_is_none_when_espn_gives_no_usable_week(value):
    respx.get(_espn_url()).mock(
        return_value=httpx.Response(200, json=_league_document(value))
    )
    assert espn.current_week(SEASON) is None


@respx.mock
def test_refresh_week_projections_ingests_espns_current_week(db_session, season_2026):
    player = Player(full_name="Josh Allen", position="QB", espn_id="3918298")
    db_session.add(player)
    db_session.commit()

    payload = {
        "players": [
            _player_entry(
                3918298,
                "Josh Allen",
                1,
                [
                    _season_stat_entry({"3": 4000.0}),
                    _week_stat_entry(1, {"3": 250.0, "4": 2.0}),
                    _week_stat_entry(2, {"3": 999.0}),
                ],
            )
        ]
    }
    respx.get(_espn_url()).mock(
        side_effect=[
            httpx.Response(200, json=_league_document(1)),
            httpx.Response(200, json=payload),
            httpx.Response(200, json={"players": []}),
        ]
    )

    result = espn.refresh_week_projections(db_session)
    assert result == {
        "season": SEASON,
        "week": 1,
        "pages": 1,
        "saved": 1,
        "matched_by_id": 1,
        "matched_by_name": 0,
        "unmatched": 0,
        "no_projection": 0,
    }

    # Only the current week is stored -- week 2's line is on the wire but ignored.
    row = db_session.query(Projection).filter(Projection.source == "espn").one()
    assert row.week == 1
    assert row.season == SEASON
    assert row.stat_json == {"pass_yds": 250.0, "pass_td": 2.0}

    log = db_session.query(SyncLog).one()
    assert log.resource == "espn_week_projections"
    assert log.status == "success"
    assert "week 1" in log.message


@respx.mock
def test_refresh_week_projections_without_a_current_week_logs_and_skips(
    db_session, season_2026
):
    respx.get(_espn_url()).mock(
        return_value=httpx.Response(200, json=_league_document(None))
    )

    result = espn.refresh_week_projections(db_session)
    assert result["week"] is None
    assert result["saved"] == 0
    assert db_session.query(Projection).count() == 0

    log = db_session.query(SyncLog).one()
    assert log.resource == "espn_week_projections"
    assert log.status == "success"
    assert "no current scoring period" in log.message


@respx.mock
def test_refresh_week_projections_logs_separately_from_the_season_pull(
    db_session, season_2026
):
    player = Player(full_name="Josh Allen", position="QB", espn_id="3918298")
    db_session.add(player)
    db_session.commit()

    payload = {
        "players": [
            _player_entry(
                3918298,
                "Josh Allen",
                1,
                [_season_stat_entry({"3": 4000.0}), _week_stat_entry(1, {"3": 250.0})],
            )
        ]
    }
    # A one-player page is shorter than PAGE_SIZE, so each refresh stops after
    # a single page: season page, then the week refresh's league document + page.
    respx.get(_espn_url()).mock(
        side_effect=[
            httpx.Response(200, json=payload),
            httpx.Response(200, json=_league_document(1)),
            httpx.Response(200, json=payload),
        ]
    )

    espn.refresh_season_projections(db_session)
    espn.refresh_week_projections(db_session)

    assert {log.resource for log in db_session.query(SyncLog).all()} == {
        "espn_projections",
        "espn_week_projections",
    }
    weeks = {row.week for row in db_session.query(Projection).all()}
    assert weeks == {None, 1}


@respx.mock
def test_refresh_projections_pages_through_multiple_pages(db_session, monkeypatch):
    monkeypatch.setattr(espn, "PAGE_SIZE", 1)

    p1 = Player(full_name="Player One", position="QB", espn_id="1")
    p2 = Player(full_name="Player Two", position="RB", espn_id="2")
    db_session.add_all([p1, p2])
    db_session.commit()

    page1 = {"players": [_player_entry(1, "Player One", 1, [_season_stat_entry({"3": 3000.0})])]}
    page2 = {"players": [_player_entry(2, "Player Two", 2, [_season_stat_entry({"24": 900.0})])]}
    page3 = {"players": []}

    respx.get(_espn_url()).mock(
        side_effect=[
            httpx.Response(200, json=page1),
            httpx.Response(200, json=page2),
            httpx.Response(200, json=page3),
        ]
    )

    result = espn.refresh_projections(db_session, SEASON, week=None)
    assert result["pages"] == 2
    assert result["saved"] == 2
    assert db_session.query(Projection).count() == 2
