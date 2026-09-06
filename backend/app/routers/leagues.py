"""League read endpoints plus owner-triggered Yahoo sync."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import League, LeaguePlayer, Matchup, Player, Team
from app.services.yahoo import oauth, sync

router = APIRouter(prefix="/api/leagues", tags=["leagues"])


class LeagueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    league_key: str
    game_key: str
    name: str
    season: int
    is_keeper: bool
    num_teams: int | None = None
    scoring_type: str | None = None
    current_week: int | None = None
    synced_at: datetime | None = None


class TeamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    team_key: str
    name: str
    manager_name: str | None = None
    is_my_team: bool
    wins: int | None = None
    losses: int | None = None
    ties: int | None = None
    rank: int | None = None
    points_for: float | None = None
    points_against: float | None = None
    logo_url: str | None = None


class FreeAgentOut(BaseModel):
    player_id: int
    full_name: str
    position: str | None = None
    nfl_team: str | None = None
    injury_status: str | None = None
    bye_week: int | None = None
    yahoo_id: str | None = None
    status: str
    percent_owned: float | None = None


class MatchupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    week: int
    home_team_id: int
    away_team_id: int | None = None
    home_points: float | None = None
    away_points: float | None = None
    is_playoffs: bool
    status: str | None = None


class DiscoverResponse(BaseModel):
    discovered: int
    leagues: list[LeagueOut]


class SyncResponse(BaseModel):
    league_key: str
    league_id: int
    week: int
    status: str
    errors: list[str] = []


def _get_league(db: Session, league_key: str) -> League:
    league = db.query(League).filter(League.league_key == league_key).one_or_none()
    if league is None:
        raise HTTPException(status_code=404, detail=f"Unknown league {league_key}")
    return league


@router.get("", response_model=list[LeagueOut])
def list_leagues(db: Session = Depends(get_db)) -> list[League]:
    return db.query(League).order_by(League.season.desc(), League.name).all()


@router.post("/discover", response_model=DiscoverResponse)
def discover(db: Session = Depends(get_db)) -> DiscoverResponse:
    try:
        leagues = sync.discover_leagues(db)
    except oauth.YahooNotConnectedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface Yahoo failures as 502
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return DiscoverResponse(
        discovered=len(leagues),
        leagues=[LeagueOut.model_validate(lg) for lg in leagues],
    )


@router.post("/{league_key}/sync", response_model=SyncResponse)
def sync_one_league(league_key: str, db: Session = Depends(get_db)) -> SyncResponse:
    try:
        result = sync.sync_league(db, league_key)
    except oauth.YahooNotConnectedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface Yahoo failures as 502
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return SyncResponse(**result)


@router.get("/{league_key}/teams", response_model=list[TeamOut])
def league_teams(league_key: str, db: Session = Depends(get_db)) -> list[Team]:
    league = _get_league(db, league_key)
    return (
        db.query(Team)
        .filter(Team.league_id == league.id)
        .order_by(Team.rank.is_(None), Team.rank, Team.name)
        .all()
    )


@router.get("/{league_key}/free-agents", response_model=list[FreeAgentOut])
def league_free_agents(
    league_key: str,
    position: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[FreeAgentOut]:
    league = _get_league(db, league_key)

    query = (
        db.query(LeaguePlayer, Player)
        .join(Player, Player.id == LeaguePlayer.player_id)
        .filter(
            LeaguePlayer.league_id == league.id,
            LeaguePlayer.status.in_(("FA", "W")),
        )
    )
    if position:
        query = query.filter(Player.position == position.upper())

    rows = (
        query.order_by(LeaguePlayer.percent_owned.desc().nullslast())
        .limit(limit)
        .all()
    )

    return [
        FreeAgentOut(
            player_id=player.id,
            full_name=player.full_name,
            position=player.position,
            nfl_team=player.nfl_team,
            injury_status=player.injury_status,
            bye_week=player.bye_week,
            yahoo_id=player.yahoo_id,
            status=league_player.status,
            percent_owned=league_player.percent_owned,
        )
        for league_player, player in rows
    ]


@router.get("/{league_key}/matchups", response_model=list[MatchupOut])
def league_matchups(
    league_key: str,
    week: int | None = Query(default=None, ge=1, le=25),
    db: Session = Depends(get_db),
) -> list[Matchup]:
    league = _get_league(db, league_key)
    target_week = week or league.current_week

    query = db.query(Matchup).filter(Matchup.league_id == league.id)
    if target_week is not None:
        query = query.filter(Matchup.week == target_week)

    return query.order_by(Matchup.week, Matchup.id).all()
