"""Standings and Monte Carlo playoff odds.

Both endpoints work for any league, manual or Yahoo-synced: they read the
``Matchup`` table, which the manual matchup editor and the Yahoo sync both
write.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import League
from app.routers.evaluate import SOURCES_DESCRIPTION
from app.services import evaluator, playoff_odds
from app.services import standings as standings_service

router = APIRouter(prefix="/api/leagues", tags=["standings"])

#: Upper bound on a single simulation request, so one URL cannot pin a core.
MAX_SIMS = 20000


class StandingsRow(BaseModel):
    team_id: int
    name: str
    is_my_team: bool
    wins: int
    losses: int
    ties: int
    points_for: float
    points_against: float
    games_played: int


class CurrentRecord(BaseModel):
    wins: int
    losses: int
    ties: int
    points_for: float


class PlayoffOddsRow(BaseModel):
    team_id: int
    name: str
    is_my_team: bool
    current: CurrentRecord
    playoff_prob: float
    avg_seed: float | None = None
    seed_1_prob: float


class PlayoffOddsResponse(BaseModel):
    league_key: str
    sims: int
    regular_season_weeks: int
    playoff_teams: int
    completed_weeks: list[int]
    teams: list[PlayoffOddsRow]


def _sources(raw: str | None) -> tuple[str, ...] | None:
    """Validated source selection, or a 400 naming the source we do not know."""
    try:
        return evaluator.validate_sources(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _get_league(db: Session, league_key: str) -> League:
    league = db.query(League).filter(League.league_key == league_key).one_or_none()
    if league is None:
        raise HTTPException(status_code=404, detail=f"Unknown league {league_key}")
    return league


@router.get("/{league_key}/standings", response_model=list[StandingsRow])
def league_standings(
    league_key: str, db: Session = Depends(get_db)
) -> list[StandingsRow]:
    """The league table, computed from entered/synced matchup results."""
    league = _get_league(db, league_key)
    return [
        StandingsRow(**row) for row in standings_service.league_standings(db, league)
    ]


@router.get("/{league_key}/playoff-odds", response_model=PlayoffOddsResponse)
def league_playoff_odds(
    league_key: str,
    sims: int = Query(default=10000, ge=1, le=MAX_SIMS),
    sources: str | None = Query(default=None, description=SOURCES_DESCRIPTION),
    db: Session = Depends(get_db),
) -> PlayoffOddsResponse:
    """Playoff probability per team, from a Monte Carlo of the rest of the season."""
    league = _get_league(db, league_key)
    result = playoff_odds.simulate(db, league, sims=sims, sources=_sources(sources))
    return PlayoffOddsResponse(
        league_key=league.league_key,
        sims=result["sims"],
        regular_season_weeks=result["regular_season_weeks"],
        playoff_teams=result["playoff_teams"],
        completed_weeks=result["completed_weeks"],
        teams=[PlayoffOddsRow(**row) for row in result["teams"]],
    )
