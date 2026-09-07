"""FantasyPros service tests. All HTTP is mocked via respx; nothing leaves the machine."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.config import settings
from app.models import Player, Projection, SyncLog
from app.services import fantasypros

SEASON = 2026


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    """Zero the polite request pacing/backoff so mocked tests stay fast."""
    monkeypatch.setattr(fantasypros, "REQUEST_PACING_SECONDS", 0)
    monkeypatch.setattr(fantasypros, "RATE_LIMIT_FALLBACK_WAIT", 0)


@pytest.fixture()
def fp_key(monkeypatch):
    monkeypatch.setattr(settings, "FANTASYPROS_API_KEY", "test-key")
    return "test-key"


def _url(season: int = SEASON) -> str:
    return f"{fantasypros.BASE_URL}/nfl/{season}/projections"


def _payload(position: str, players: list[dict]) -> dict:
    return {
        "season": str(SEASON),
        "week": "0",
        "count": str(len(players)),
        "positions": position,
        "scoring": "STD",
        "experts": [1, 2, 3],
        "players": players,
    }


def _player(fpid: int, name: str, position_id: str, stats: dict) -> dict:
    return {
        "fpid": str(fpid),
        "mflid": str(fpid),
        "name": name,
        "position_id": position_id,
        "team_id": "BUF",
        "filename": "x.php",
        "stats": [stats],
    }


def _mock_all_positions(empty_positions: set[str] | None = None, **by_position):
    empty_positions = empty_positions or set()
    for position in fantasypros.POSITIONS:
        players = by_position.get(position, [])
        respx.get(_url(), params={"position": position, "week": "0"}).mock(
            return_value=httpx.Response(200, json=_payload(position, players))
        )


# --------------------------------------------------------------------------- #
# missing key
# --------------------------------------------------------------------------- #


def test_refresh_projections_without_key_raises(db_session, monkeypatch):
    monkeypatch.setattr(settings, "FANTASYPROS_API_KEY", "")
    with pytest.raises(fantasypros.FantasyProsNotConfiguredError):
        fantasypros.refresh_projections(db_session, SEASON)

    # No request attempted, no SyncLog row written.
    assert db_session.query(SyncLog).count() == 0


# --------------------------------------------------------------------------- #
# stat translation
# --------------------------------------------------------------------------- #


def test_translate_stat_line_qb():
    raw = {
        "points": 300.0,
        "pass_att": 500.0,
        "pass_cmp": 320.0,
        "pass_yds": 4000.0,
        "pass_tds": 28.0,
        "pass_ints": 10.0,
        "rush_att": 40.0,
        "rush_yds": 200.0,
        "rush_tds": 2.0,
        "fumbles": 3.0,
        "2pt_tds": 1.0,
    }
    result = fantasypros.translate_stat_line("QB", raw)
    assert result == {
        "pass_att": 500.0,
        "pass_cmp": 320.0,
        "pass_yds": 4000.0,
        "pass_td": 28.0,
        "pass_int": 10.0,
        "rush_att": 40.0,
        "rush_yds": 200.0,
        "rush_td": 2.0,
        "fum_lost": 3.0,
        "pass_2pt": 1.0,
    }


def test_translate_stat_line_rb_wr_te():
    rb = fantasypros.translate_stat_line(
        "RB", {"rush_att": 250.0, "rush_yds": 1200.0, "rush_tds": 10.0, "rec_rec": 40.0}
    )
    assert rb["rush_yds"] == 1200.0
    assert rb["rec"] == 40.0

    wr = fantasypros.translate_stat_line(
        "WR", {"rec_rec": 90.0, "rec_yds": 1300.0, "rec_tds": 9.0, "2pt_tds": 1.0}
    )
    assert wr == {"rec": 90.0, "rec_yds": 1300.0, "rec_td": 9.0, "rec_2pt": 1.0}


def test_translate_stat_line_kicker_and_dst():
    k = fantasypros.translate_stat_line("K", {"fga": 38.0, "fg": 34.0, "xpt": 40.0})
    assert k == {"fg_made": 34.0, "xp_made": 40.0}

    dst = fantasypros.translate_stat_line(
        "DST",
        {
            "def_sack": 47.0,
            "def_int": 14.0,
            "def_td": 2.0,
            "def_retd": 1.0,
            "def_safety": 1.0,
            "def_ff": 20.0,
            "def_fr": 15.0,
            "def_pa_a": 5.0,
        },
    )
    assert dst == {
        "dst_sack": 47.0,
        "dst_int": 14.0,
        "dst_fum_rec": 15.0,
        "dst_safety": 1.0,
        "dst_td": 3.0,  # def_td + def_retd
    }


# --------------------------------------------------------------------------- #
# end-to-end with mocked key + response
# --------------------------------------------------------------------------- #


@respx.mock
def test_refresh_projections_matches_by_id_and_upserts(db_session, fp_key):
    player = Player(full_name="Josh Allen", position="QB", fantasypros_id="7", espn_id=None)
    db_session.add(player)
    db_session.commit()

    _mock_all_positions(
        QB=[_player(7, "A Different Name", "QB", {"pass_yds": 4000.0, "pass_tds": 30.0})]
    )

    result = fantasypros.refresh_projections(db_session, SEASON, week=None)
    assert result["matched_by_id"] == 1
    assert result["saved"] == 1

    row = db_session.query(Projection).filter(Projection.source == "fantasypros").one()
    assert row.player_id == player.id
    assert row.stat_json == {"pass_yds": 4000.0, "pass_td": 30.0}
    assert row.week is None

    log = db_session.query(SyncLog).filter(SyncLog.resource == "fantasypros_projections").one()
    assert log.status == "success"


@respx.mock
def test_refresh_projections_matches_by_name_and_backfills_id(db_session, fp_key):
    player = Player(full_name="Ja'Marr Chase", position="WR", fantasypros_id=None)
    db_session.add(player)
    db_session.commit()

    _mock_all_positions(
        WR=[_player(99, "Ja'Marr Chase", "WR", {"rec_rec": 95.0, "rec_yds": 1400.0})]
    )

    result = fantasypros.refresh_projections(db_session, SEASON, week=None)
    assert result["matched_by_name"] == 1
    assert result["matched_by_id"] == 0
    assert db_session.query(Player).count() == 1

    db_session.refresh(player)
    assert player.fantasypros_id == "99"


@respx.mock
def test_refresh_projections_unmatched_player_skipped(db_session, fp_key):
    _mock_all_positions(TE=[_player(55, "Nobody Anyone Knows", "TE", {"rec_rec": 10.0})])

    result = fantasypros.refresh_projections(db_session, SEASON, week=None)
    assert result["unmatched"] == 1
    assert result["saved"] == 0
    assert db_session.query(Player).count() == 0


@respx.mock
def test_refresh_projections_week_param_and_upsert_idempotent(db_session, fp_key):
    player = Player(full_name="Josh Allen", position="QB", fantasypros_id="7")
    db_session.add(player)
    db_session.commit()

    for position in fantasypros.POSITIONS:
        players = (
            [_player(7, "Josh Allen", "QB", {"pass_yds": 250.0, "pass_tds": 2.0})]
            if position == "QB"
            else []
        )
        respx.get(_url(), params={"position": position, "week": "5"}).mock(
            return_value=httpx.Response(200, json=_payload(position, players))
        )

    fantasypros.refresh_projections(db_session, SEASON, week=5)
    fantasypros.refresh_projections(db_session, SEASON, week=5)

    rows = db_session.query(Projection).filter(
        Projection.source == "fantasypros", Projection.week == 5
    ).all()
    assert len(rows) == 1
    assert rows[0].stat_json == {"pass_yds": 250.0, "pass_td": 2.0}

    # A rest-of-season row (week=None) is a separate row entirely.
    _mock_all_positions(
        QB=[_player(7, "Josh Allen", "QB", {"pass_yds": 4000.0, "pass_tds": 30.0})]
    )
    fantasypros.refresh_projections(db_session, SEASON, week=None)

    all_rows = db_session.query(Projection).filter(Projection.source == "fantasypros").all()
    assert len(all_rows) == 2
    assert {r.week for r in all_rows} == {5, None}
