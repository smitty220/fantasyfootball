"""Evaluation endpoints: free-agent rankings and trade analysis."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import League, Team
from app.services import evaluator
from app.services import trade_finder as trade_finder_service
from app.services.yahoo.sync import current_nfl_season

router = APIRouter(prefix="/api/leagues", tags=["evaluate"])

#: Projection-source metadata lives outside the per-league tree.
projections_router = APIRouter(prefix="/api/projections", tags=["evaluate"])

SOURCES_DESCRIPTION = (
    "Comma-separated projection sources to use (e.g. \"fantasypros,espn\"). "
    "Several sources are averaged; omit for the default best-source pick."
)


# --- schemas ---------------------------------------------------------------


class ScheduleFields(BaseModel):
    """NFL-schedule context every player row carries.

    All three read as "unknown" rather than "no" when we have no schedule for
    the response's week: null ``opponent`` and ``on_bye`` false. ``bye_week``
    comes off the player row (kept current by
    ``app.services.nfl_schedule.refresh_bye_weeks``), the other two from this
    week's games.
    """

    #: The player's team's bye week this season; null until the schedule loads.
    bye_week: int | None = None
    #: This week's matchup as "vs SEA" / "@ SEA"; null on a bye or with no data.
    opponent: str | None = None
    #: True only when we have this week's schedule and the team has no game.
    on_bye: bool = False


class FreeAgentRow(ScheduleFields):
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


class MyPlayerRow(ScheduleFields):
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


class LineupPlayer(ScheduleFields):
    player_id: int
    full_name: str
    position: str | None = None
    nfl_team: str | None = None
    week_points: float | None = None
    ros_points: float
    injury_status: str | None = None
    percent_owned: float | None = None
    percent_started: float | None = None
    #: Best same-position free agent's week points, when it beats this player's.
    better_fa_week_points: float | None = None
    #: Same, for rest-of-season points.
    better_fa_ros_points: float | None = None


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
    #: Projection sources to average; omitted/empty means the best-source pick.
    sources: list[str] | None = None


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


class TradeFinderPlayer(BaseModel):
    player_id: int
    full_name: str
    position: str | None = None
    ros_points: float
    #: FantasyCalc redraft value; null when the market has no price on file.
    value: float | None = None


class TradeFinderOpponent(BaseModel):
    team_id: int
    name: str


class TradeFinderTeam(BaseModel):
    id: int
    name: str


class TradeSuggestion(BaseModel):
    opponent: TradeFinderOpponent
    #: Players I would give up.
    sends: list[TradeFinderPlayer]
    #: Players I would get back.
    receives: list[TradeFinderPlayer]
    #: Change in my optimal lineup's ROS points; always positive here.
    my_lineup_delta: float
    #: Same for the opponent -- never far below zero, or they would decline.
    opp_lineup_delta: float
    #: How far apart the two sides' market values are, as a fraction.
    value_margin_pct: float
    #: "1for1" / "2for1" (I send two) / "1for2" (I send one).
    kind: str


class TradeFinderResponse(BaseModel):
    league_key: str
    season: int
    my_team: TradeFinderTeam
    suggestions: list[TradeSuggestion]


class ProjectionSourceRow(BaseModel):
    source: str
    #: When this source's season-long rows were last fetched; null if it has none.
    season_updated_at: datetime | None = None
    #: Same for its rows describing the current projection week.
    week_updated_at: datetime | None = None


class ProjectionSourcesResponse(BaseModel):
    week: int | None = None
    sources: list[ProjectionSourceRow]


# --- helpers ---------------------------------------------------------------


def _get_league(db: Session, league_key: str) -> League:
    league = db.query(League).filter(League.league_key == league_key).one_or_none()
    if league is None:
        raise HTTPException(status_code=404, detail=f"Unknown league {league_key}")
    return league


def _sources(raw: str | None) -> tuple[str, ...] | None:
    """Validated source selection, or a 400 naming the source we do not know."""
    try:
        return evaluator.validate_sources(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def lineup_response(
    db: Session,
    league: League,
    team_id: int,
    season: int | None = None,
    sources: tuple[str, ...] | None = None,
) -> TeamLineupResponse:
    """The lineup payload for one team.

    Shared with the manual-league lineup editor so saving a lineup answers in
    exactly the shape the GET returns.
    """
    result = evaluator.team_lineup(
        db,
        league,
        team_id,
        season=season if season is not None else current_nfl_season(),
        sources=sources,
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
    sources: str | None = Query(default=None, description=SOURCES_DESCRIPTION),
    db: Session = Depends(get_db),
) -> FreeAgentsResponse:
    league = _get_league(db, league_key)
    season = current_nfl_season()

    result = evaluator.evaluate_free_agents(
        db,
        league,
        position=position,
        limit=limit,
        season=season,
        sources=_sources(sources),
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
    sources: str | None = Query(default=None, description=SOURCES_DESCRIPTION),
    db: Session = Depends(get_db),
) -> TeamLineupResponse:
    """One team's starting lineup: the owner's if saved, else ROS-optimal."""
    league = _get_league(db, league_key)
    team = _get_team(db, league, team_id)
    return lineup_response(db, league, team.id, sources=_sources(sources))


@router.post("/{league_key}/evaluate/trade", response_model=TradeResponse)
def trade_analysis(
    league_key: str,
    payload: TradeRequest,
    db: Session = Depends(get_db),
) -> TradeResponse:
    league = _get_league(db, league_key)
    sources = _sources(",".join(payload.sources) if payload.sources else None)

    try:
        result = evaluator.evaluate_trade(
            db,
            league,
            payload.side_a.model_dump(),
            payload.side_b.model_dump(),
            season=current_nfl_season(),
            sources=sources,
        )
    except evaluator.TradeValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return TradeResponse(**result)


@router.get(
    "/{league_key}/evaluate/trade-finder", response_model=TradeFinderResponse
)
def trade_finder(
    league_key: str,
    limit: int = Query(default=10, ge=1, le=25),
    sources: str | None = Query(default=None, description=SOURCES_DESCRIPTION),
    db: Session = Depends(get_db),
) -> TradeFinderResponse:
    """Trades worth proposing between my team and every other team.

    Ranked by how much each one improves my optimal lineup; every suggestion
    is close to fair on market value and leaves the other manager no worse
    off, so they are offers that could plausibly be accepted.

    409 when the league has no team flagged as mine -- there is nobody to
    trade for until one is picked.
    """
    league = _get_league(db, league_key)
    season = current_nfl_season()

    try:
        result = trade_finder_service.find_trades(
            db,
            league,
            sources=_sources(sources),
            limit=limit,
            season=season,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return TradeFinderResponse(
        league_key=league.league_key,
        season=season,
        my_team=TradeFinderTeam(**result["my_team"]),
        suggestions=[TradeSuggestion(**row) for row in result["suggestions"]],
    )


@projections_router.get("/sources", response_model=ProjectionSourcesResponse)
def projection_sources(db: Session = Depends(get_db)) -> ProjectionSourcesResponse:
    """Which projection sources are selectable, and how fresh each one is."""
    result = evaluator.projection_sources(db, current_nfl_season())
    return ProjectionSourcesResponse(
        week=result["week"],
        sources=[ProjectionSourceRow(**row) for row in result["sources"]],
    )
