"""Evaluation endpoints: free-agent rankings and trade analysis."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import League, Team
from app.services import evaluator
from app.services.yahoo.sync import current_nfl_season

router = APIRouter(prefix="/api/leagues", tags=["evaluate"])


# --- schemas ---------------------------------------------------------------


class FreeAgentRow(BaseModel):
    player_id: int
    full_name: str
    position: str | None = None
    nfl_team: str | None = None
    injury_status: str | None = None
    has_projection: bool
    ros_points: float
    ppg: float
    week_points: float | None = None
    week_delta: float | None = None
    vor: float
    trade_value: float | None = None
    trending_add: int | None = None
    my_worst_starter_delta: float | None = None


class MyPlayerRow(BaseModel):
    player_id: int
    full_name: str
    position: str | None = None
    nfl_team: str | None = None
    injury_status: str | None = None
    ros_points: float
    ppg: float
    week_points: float | None = None
    is_starter: bool
    starter_slot: str | None = None


class FreeAgentsResponse(BaseModel):
    league_key: str
    season: int
    week: int | None = None
    rows: list[FreeAgentRow]
    my_players: list[MyPlayerRow] = Field(default_factory=list)


class LineupPlayer(BaseModel):
    player_id: int
    full_name: str
    position: str | None = None
    nfl_team: str | None = None
    week_points: float | None = None
    ros_points: float


class LineupSlot(BaseModel):
    """One seat in the starting lineup; ``player`` is null when it is empty."""

    slot: str
    player: LineupPlayer | None = None


class TeamLineupResponse(BaseModel):
    league_key: str
    team_id: int
    week: int | None = None
    #: "manual" when the owner saved this lineup, "auto" when we computed it.
    source: str
    slots: list[LineupSlot]
    bench: list[LineupPlayer]


class TradeSideIn(BaseModel):
    team_id: int
    player_ids: list[int] = Field(default_factory=list)


class TradeRequest(BaseModel):
    side_a: TradeSideIn
    side_b: TradeSideIn


class TradePlayerOut(BaseModel):
    player_id: int
    full_name: str
    position: str | None = None
    ros_points: float
    ppg: float
    value: float | None = None
    dynasty_value: float | None = None


class TradeSideOut(BaseModel):
    team_id: int
    team_name: str
    players: list[TradePlayerOut]
    ros_points_total: float
    value_total: float
    lineup_points_before: float
    lineup_points_after: float
    lineup_delta: float


class TradeSides(BaseModel):
    a: TradeSideOut
    b: TradeSideOut


class TradeResponse(BaseModel):
    verdict: str
    margin_pct: float
    sides: TradeSides
    notes: list[str]


# --- helpers ---------------------------------------------------------------


def _get_league(db: Session, league_key: str) -> League:
    league = db.query(League).filter(League.league_key == league_key).one_or_none()
    if league is None:
        raise HTTPException(status_code=404, detail=f"Unknown league {league_key}")
    return league


def lineup_response(
    db: Session, league: League, team_id: int, season: int | None = None
) -> TeamLineupResponse:
    """The lineup payload for one team.

    Shared with the manual-league lineup editor so saving a lineup answers in
    exactly the shape the GET returns.
    """
    result = evaluator.team_lineup(
        db, league, team_id, season=season if season is not None else current_nfl_season()
    )
    return TeamLineupResponse(
        league_key=league.league_key,
        team_id=team_id,
        week=result["week"],
        source=result["source"],
        slots=[LineupSlot(**slot) for slot in result["slots"]],
        bench=[LineupPlayer(**row) for row in result["bench"]],
    )


def _get_team(db: Session, league: League, team_id: int) -> Team:
    team = db.query(Team).filter(Team.id == team_id).one_or_none()
    if team is None or team.league_id != league.id:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown team {team_id} in league {league.league_key}",
        )
    return team


# --- endpoints -------------------------------------------------------------


@router.get("/{league_key}/evaluate/free-agents", response_model=FreeAgentsResponse)
def free_agent_rankings(
    league_key: str,
    position: str | None = Query(
        default=None,
        description='Position to filter by; "FLEX" (or "W/R/T") means RB/WR/TE.',
    ),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> FreeAgentsResponse:
    league = _get_league(db, league_key)
    season = current_nfl_season()

    result = evaluator.evaluate_free_agents(
        db, league, position=position, limit=limit, season=season
    )
    return FreeAgentsResponse(
        league_key=league.league_key,
        season=season,
        week=result["week"],
        rows=[FreeAgentRow(**row) for row in result["rows"]],
        my_players=[MyPlayerRow(**row) for row in result["my_players"]],
    )


@router.get(
    "/{league_key}/evaluate/teams/{team_id}/lineup",
    response_model=TeamLineupResponse,
)
def team_lineup(
    league_key: str,
    team_id: int,
    db: Session = Depends(get_db),
) -> TeamLineupResponse:
    """One team's starting lineup: the owner's if saved, else ROS-optimal."""
    league = _get_league(db, league_key)
    team = _get_team(db, league, team_id)
    return lineup_response(db, league, team.id)


@router.post("/{league_key}/evaluate/trade", response_model=TradeResponse)
def trade_analysis(
    league_key: str,
    payload: TradeRequest,
    db: Session = Depends(get_db),
) -> TradeResponse:
    league = _get_league(db, league_key)

    try:
        result = evaluator.evaluate_trade(
            db,
            league,
            payload.side_a.model_dump(),
            payload.side_b.model_dump(),
            season=current_nfl_season(),
        )
    except evaluator.TradeValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return TradeResponse(**result)
