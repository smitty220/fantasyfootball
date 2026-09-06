"""Player search endpoint: name matching + trade-value ordering."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models import Player, TradeValue


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture()
def seeded_players(db_session):
    high = Player(full_name="Ja'Marr Chase", position="WR", nfl_team="CIN")
    low = Player(full_name="Jaylen Warren", position="WR", nfl_team="PIT")
    none = Player(full_name="Jameson Williams", position="WR", nfl_team="DET")
    other_position = Player(full_name="Jalen Hurts", position="QB", nfl_team="PHI")
    punctuation_target = Player(full_name="AJ Brown", position="WR", nfl_team="PHI")
    db_session.add_all([high, low, none, other_position, punctuation_target])
    db_session.commit()

    db_session.add_all(
        [
            TradeValue(
                player_id=high.id,
                source="fantasycalc",
                format="redraft",
                value=9000,
                fetched_at=_utcnow(),
            ),
            TradeValue(
                player_id=low.id,
                source="fantasycalc",
                format="redraft",
                value=4000,
                fetched_at=_utcnow(),
            ),
            # a dynasty-only value should never affect redraft search ordering
            TradeValue(
                player_id=none.id,
                source="fantasycalc",
                format="dynasty",
                value=9999,
                fetched_at=_utcnow(),
            ),
        ]
    )
    db_session.commit()
    return {"high": high, "low": low, "none": none, "other_position": other_position}


def test_search_orders_by_trade_value_desc_nulls_last(client, seeded_players):
    body = client.get("/api/players/search?q=ja&position=wr").json()
    names = [p["full_name"] for p in body]
    assert names == ["Ja'Marr Chase", "Jaylen Warren", "Jameson Williams"]
    assert body[0]["trade_value"] == 9000
    assert body[1]["trade_value"] == 4000
    assert body[2]["trade_value"] is None


def test_search_case_insensitive_substring(client, seeded_players):
    body = client.get("/api/players/search?q=WARREN").json()
    assert [p["full_name"] for p in body] == ["Jaylen Warren"]


def test_search_position_filter(client, seeded_players):
    body = client.get("/api/players/search?q=ja&position=qb").json()
    assert [p["full_name"] for p in body] == ["Jalen Hurts"]


def test_search_strips_punctuation_from_query(client, seeded_players):
    # "AJ Brown" has no punctuation; a query with punctuation should still
    # match once stripped.
    body = client.get("/api/players/search?q=A.J.").json()
    assert any(p["full_name"] == "AJ Brown" for p in body)


def test_search_requires_min_length(client):
    assert client.get("/api/players/search?q=a").status_code == 422


def test_search_limit(client, seeded_players):
    body = client.get("/api/players/search?q=ja&limit=1").json()
    assert len(body) == 1
    assert body[0]["full_name"] == "Ja'Marr Chase"
