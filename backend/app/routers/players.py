"""Player search: used by manual-league roster/free-agent pickers."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import League, Player, TradeValue
from app.services import fantasycalc

router = APIRouter(prefix="/api/players", tags=["players"])

_PUNCTUATION_RE = re.compile(r"[.'’]")


class PlayerSearchOut(BaseModel):
    id: int
    full_name: str
    position: str | None = None
    nfl_team: str | None = None
    injury_status: str | None = None
    trade_value: float | None = None


@router.get("/search", response_model=list[PlayerSearchOut])
def search_players(
    q: str = Query(..., min_length=2),
    position: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    league_key: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[PlayerSearchOut]:
    # Values are per league profile (teams / QBs / PPR). Use the named league's;
    # with none given (the picker is league-agnostic) fall back to the oldest
    # league's profile, or FantasyCalc's default profile when there are none.
    league_query = db.query(League)
    if league_key:
        league_query = league_query.filter(League.league_key == league_key)
    value_format = fantasycalc.trade_format(league_query.order_by(League.id).first())
    q_stripped = _PUNCTUATION_RE.sub("", q)

    query = (
        db.query(Player, TradeValue.value)
        .outerjoin(
            TradeValue,
            (TradeValue.player_id == Player.id)
            & (TradeValue.source == "fantasycalc")
            & (TradeValue.format == value_format),
        )
        .filter(
            Player.full_name.ilike(f"%{q}%")
            | Player.full_name.ilike(f"%{q_stripped}%")
        )
    )

    if position:
        query = query.filter(Player.position == position.upper())

    rows = (
        query.order_by(TradeValue.value.desc().nullslast(), Player.full_name)
        .limit(limit)
        .all()
    )

    return [
        PlayerSearchOut(
            id=player.id,
            full_name=player.full_name,
            position=player.position,
            nfl_team=player.nfl_team,
            injury_status=player.injury_status,
            trade_value=value,
        )
        for player, value in rows
    ]
