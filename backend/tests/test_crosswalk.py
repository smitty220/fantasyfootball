"""Crosswalk service tests. No network: _fetch_csv_text is monkeypatched."""

from __future__ import annotations

import pytest

from app.models import Player, SyncLog
from app.services import crosswalk

CSV_HEADER = (
    "mfl_id,sportradar_id,fantasypros_id,gsis_id,pff_id,sleeper_id,nfl_id,"
    "espn_id,yahoo_id,fleaflicker_id,cbs_id,pfr_id,cfbref_id,rotowire_id,"
    "rotoworld_id,ktc_id,stats_id,stats_global_id,fantasy_data_id,swish_id,"
    "name,merge_name,position,team,birthdate,age,draft_year,draft_round,"
    "draft_pick,draft_ovr,twitter_username,height,weight,college,db_season"
)


def _csv_row(
    name: str,
    merge_name: str,
    position: str,
    team: str = "SF",
    yahoo_id: str = "NA",
    sleeper_id: str = "NA",
    espn_id: str = "NA",
    fantasypros_id: str = "NA",
    gsis_id: str = "NA",
) -> str:
    fields = {
        "mfl_id": "1",
        "sportradar_id": "NA",
        "fantasypros_id": fantasypros_id,
        "gsis_id": gsis_id,
        "pff_id": "NA",
        "sleeper_id": sleeper_id,
        "nfl_id": "NA",
        "espn_id": espn_id,
        "yahoo_id": yahoo_id,
        "fleaflicker_id": "NA",
        "cbs_id": "NA",
        "pfr_id": "NA",
        "cfbref_id": "NA",
        "rotowire_id": "NA",
        "rotoworld_id": "NA",
        "ktc_id": "NA",
        "stats_id": "NA",
        "stats_global_id": "NA",
        "fantasy_data_id": "NA",
        "swish_id": "NA",
        "name": name,
        "merge_name": merge_name,
        "position": position,
        "team": team,
        "birthdate": "NA",
        "age": "NA",
        "draft_year": "NA",
        "draft_round": "NA",
        "draft_pick": "NA",
        "draft_ovr": "NA",
        "twitter_username": "NA",
        "height": "NA",
        "weight": "NA",
        "college": "NA",
        "db_season": "2026",
    }
    return ",".join(fields[col] for col in CSV_HEADER.split(","))


def _csv(*rows: str) -> str:
    return "\n".join((CSV_HEADER, *rows))


@pytest.fixture(autouse=True)
def no_real_download(monkeypatch):
    """Ensure a test that forgets to stub the CSV fails loudly, not by hitting the network."""

    def _boom():
        raise AssertionError("crosswalk._fetch_csv_text was not monkeypatched")

    monkeypatch.setattr(crosswalk, "_fetch_csv_text", _boom)


def test_refresh_crosswalk_creates_new_player(db_session, monkeypatch):
    csv_text = _csv(
        _csv_row(
            "Jahmyr Gibbs",
            "jahmyr gibbs",
            "RB",
            team="DET",
            yahoo_id="12345",
            sleeper_id="9221",
        )
    )
    monkeypatch.setattr(crosswalk, "_fetch_csv_text", lambda: csv_text)

    message = crosswalk.refresh_crosswalk(db_session)

    player = db_session.query(Player).one()
    assert player.full_name == "Jahmyr Gibbs"
    assert player.position == "RB"
    assert player.nfl_team == "DET"
    assert player.yahoo_id == "12345"
    assert player.sleeper_id == "9221"
    assert "1 created" in message

    log = db_session.query(SyncLog).filter(SyncLog.resource == "crosswalk").one()
    assert log.status == "success"


def test_refresh_crosswalk_enriches_null_ids_without_overwriting(db_session, monkeypatch):
    # Matched via yahoo_id; already has a (different) sleeper_id, and a NULL
    # espn_id.
    existing = Player(
        full_name="Jahmyr Gibbs",
        position="RB",
        yahoo_id="12345",
        sleeper_id="OLD-SLEEPER-ID",
    )
    db_session.add(existing)
    db_session.commit()

    csv_text = _csv(
        _csv_row(
            "Jahmyr Gibbs",
            "jahmyr gibbs",
            "RB",
            team="DET",
            yahoo_id="12345",  # matches -> resolves to `existing`
            sleeper_id="9221",  # differs from existing -> must NOT overwrite
            espn_id="4429795",  # NULL on existing -> should be filled
        )
    )
    monkeypatch.setattr(crosswalk, "_fetch_csv_text", lambda: csv_text)

    crosswalk.refresh_crosswalk(db_session)

    db_session.refresh(existing)
    assert existing.yahoo_id == "12345"  # untouched
    assert existing.sleeper_id == "OLD-SLEEPER-ID"  # never overwritten
    assert existing.espn_id == "4429795"  # filled
    assert db_session.query(Player).count() == 1


def test_refresh_crosswalk_name_match_fallback(db_session, monkeypatch):
    existing = Player(full_name="Amon-Ra St. Brown", position="WR")
    db_session.add(existing)
    db_session.commit()

    csv_text = _csv(
        _csv_row(
            "Amon-Ra St. Brown",
            "amon-ra st brown",
            "WR",
            team="DET",
            sleeper_id="7564",
        )
    )
    monkeypatch.setattr(crosswalk, "_fetch_csv_text", lambda: csv_text)

    crosswalk.refresh_crosswalk(db_session)

    db_session.refresh(existing)
    assert existing.sleeper_id == "7564"


def test_refresh_crosswalk_skips_ambiguous_name_match(db_session, monkeypatch):
    existing = Player(full_name="Mike Williams", position="WR")
    db_session.add(existing)
    db_session.commit()

    csv_text = _csv(
        _csv_row("Mike Williams", "mike williams", "WR", team="NYJ", sleeper_id="111"),
        _csv_row("Mike Williams", "mike williams", "WR", team="LAC", sleeper_id="222"),
    )
    monkeypatch.setattr(crosswalk, "_fetch_csv_text", lambda: csv_text)

    crosswalk.refresh_crosswalk(db_session)

    db_session.refresh(existing)
    assert existing.sleeper_id is None
    # Both CSV rows also produce new Player rows (no existing match by ID).
    assert db_session.query(Player).count() == 3


def test_refresh_crosswalk_ignores_irrelevant_positions(db_session, monkeypatch):
    csv_text = _csv(_csv_row("Some Linebacker", "some linebacker", "LB"))
    monkeypatch.setattr(crosswalk, "_fetch_csv_text", lambda: csv_text)

    crosswalk.refresh_crosswalk(db_session)

    assert db_session.query(Player).count() == 0


def test_refresh_crosswalk_maps_pk_to_k(db_session, monkeypatch):
    csv_text = _csv(
        _csv_row("Brandon Aubrey", "brandon aubrey", "PK", team="DAL", sleeper_id="11533")
    )
    monkeypatch.setattr(crosswalk, "_fetch_csv_text", lambda: csv_text)

    crosswalk.refresh_crosswalk(db_session)

    player = db_session.query(Player).one()
    assert player.position == "K"
