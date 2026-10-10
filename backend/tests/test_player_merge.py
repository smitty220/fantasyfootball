"""Folding Yahoo-only duplicate player rows into their canonical row."""

from __future__ import annotations

from app.models import (
    DraftPick,
    League,
    LeaguePlayer,
    Player,
    Projection,
    RosterSlot,
    Team,
    TradeValue,
)
from app.services import player_merge
from tests.conftest import utcnow


def _league_and_team(db) -> tuple[League, Team]:
    league = League(league_key="470.l.1", game_key="470", name="L", season=2026, source="yahoo")
    db.add(league)
    db.flush()
    team = Team(team_key="470.l.1.t.1", league_id=league.id, name="Mine")
    db.add(team)
    db.flush()
    return league, team


def _rookie_pair(db) -> tuple[Player, Player]:
    keep = Player(full_name="Ashton Jeanty", position="RB", nfl_team="LV", sleeper_id="12")
    dup = Player(full_name="Ashton Jeanty", position="RB", nfl_team="LV", yahoo_id="40001")
    db.add_all([keep, dup])
    db.flush()
    return dup, keep


def test_pairs_yahoo_only_rows_with_their_single_canonical_row(db_session):
    dup, keep = _rookie_pair(db_session)
    burden_keep = Player(full_name="Luther Burden", position="WR", nfl_team="CHI", sleeper_id="9")
    burden_dup = Player(full_name="Luther Burden III", position="WR", nfl_team="Chi", yahoo_id="40002")
    saints_keep = Player(full_name="New Orleans Saints", position="DEF", nfl_team="NO", sleeper_id="NO")
    saints_dup = Player(full_name="Saints", position="DEF", nfl_team="NO", yahoo_id="100018")
    db_session.add_all([burden_keep, burden_dup, saints_keep, saints_dup])
    db_session.commit()

    pairs = {d.id: k.id for d, k in player_merge.find_yahoo_duplicates(db_session)}

    assert pairs == {dup.id: keep.id, burden_dup.id: burden_keep.id, saints_dup.id: saints_keep.id}


def test_skips_ambiguous_team_mismatched_and_already_linked_rows(db_session):
    # Two canonical candidates: ambiguous, never guess.
    db_session.add_all(
        [
            Player(full_name="Josh Johnson", position="QB", nfl_team="BAL", sleeper_id="1"),
            Player(full_name="Josh Johnson", position="QB", nfl_team="BAL", sleeper_id="2"),
            Player(full_name="Josh Johnson", position="QB", nfl_team="BAL", yahoo_id="1"),
            # Same name, different team: a different player.
            Player(full_name="Mike Williams", position="WR", nfl_team="LAC", sleeper_id="3"),
            Player(full_name="Mike Williams", position="WR", nfl_team="NYJ", yahoo_id="2"),
            # Already linked to another platform: not a Yahoo-only duplicate.
            Player(full_name="Brock Purdy", position="QB", nfl_team="SF", sleeper_id="4"),
            Player(full_name="Brock Purdy", position="QB", nfl_team="SF", yahoo_id="3", espn_id="5"),
        ]
    )
    db_session.commit()

    assert player_merge.find_yahoo_duplicates(db_session) == []


def test_merge_moves_league_data_keeps_projections_and_adopts_yahoo_id(db_session):
    league, team = _league_and_team(db_session)
    dup, keep = _rookie_pair(db_session)
    db_session.add_all(
        [
            LeaguePlayer(league_id=league.id, player_id=dup.id, status="T", on_team_id=team.id),
            RosterSlot(team_id=team.id, week=5, player_id=dup.id, selected_position="RB"),
            DraftPick(league_id=league.id, round=1, pick=3, team_id=team.id, player_id=dup.id),
            Projection(player_id=keep.id, source="fantasypros", season=2026, week=None,
                       stat_json={"rush_yds": 900}, fetched_at=utcnow()),
            TradeValue(player_id=keep.id, source="fantasycalc", format="redraft", value=6350.0,
                       fetched_at=utcnow()),
        ]
    )
    db_session.commit()
    dup_id, keep_id = dup.id, keep.id

    player_merge.merge_player(db_session, dup, keep)
    db_session.commit()

    assert db_session.get(Player, dup_id) is None
    assert db_session.get(Player, keep_id).yahoo_id == "40001"
    assert db_session.query(LeaguePlayer).one().player_id == keep_id
    assert db_session.query(LeaguePlayer).one().on_team_id == team.id
    assert db_session.query(RosterSlot).one().player_id == keep_id
    assert db_session.query(DraftPick).one().player_id == keep_id
    assert db_session.query(Projection).one().player_id == keep_id
    assert db_session.query(TradeValue).one().player_id == keep_id


def test_merge_clash_keeps_the_duplicates_fresh_league_row(db_session):
    league, team = _league_and_team(db_session)
    dup, keep = _rookie_pair(db_session)
    db_session.add_all(
        [
            LeaguePlayer(league_id=league.id, player_id=keep.id, status="FA"),
            LeaguePlayer(league_id=league.id, player_id=dup.id, status="T", on_team_id=team.id),
        ]
    )
    db_session.commit()

    player_merge.merge_player(db_session, dup, keep)
    db_session.commit()

    row = db_session.query(LeaguePlayer).one()
    assert (row.player_id, row.status, row.on_team_id) == (keep.id, "T", team.id)


def test_cli_dry_run_changes_nothing_and_only_limits_the_apply(db_session, monkeypatch, capsys):
    jeanty_dup, _ = _rookie_pair(db_session)
    other_keep = Player(full_name="Tyler Warren", position="TE", nfl_team="IND", sleeper_id="7")
    other_dup = Player(full_name="Tyler Warren", position="TE", nfl_team="Ind", yahoo_id="40003")
    db_session.add_all([other_keep, other_dup])
    db_session.commit()
    jeanty_id, warren_id = jeanty_dup.id, other_dup.id

    import app.db

    monkeypatch.setattr(app.db, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    player_merge.main([])
    assert "2 pair(s)" in capsys.readouterr().out
    assert db_session.query(Player).count() == 4

    player_merge.main(["--only", str(jeanty_id), "--apply"])
    db_session.expire_all()
    assert db_session.get(Player, jeanty_id) is None
    assert db_session.get(Player, warren_id) is not None
