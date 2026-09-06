"""Player search: used by manual-league roster/free-agent pickers."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Player, TradeValue

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
    db: Session = Depends(get_db),
) -> list[PlayerSearchOut]:
    q_stripped = _PUNCTUATION_RE.sub("", q)

    query = (
        db.query(Player, TradeValue.value)
        .outerjoin(
            TradeValue,
            (TradeValue.player_id == Player.id)
            & (TradeValue.source == "fantasycalc")
            & (TradeValue.format == "redraft"),
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
