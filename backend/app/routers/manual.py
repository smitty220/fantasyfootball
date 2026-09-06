"""Manual league entry: CRUD for leagues/teams/rosters not backed by Yahoo.

Lets the owner stand up a league by hand (name, scoring rules, teams,
rosters) so the evaluators are usable before Yahoo API approval lands.
Every endpoint here operates only on ``League.source == "manual"`` rows and
refuses (404/409) to touch anything synced from Yahoo.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import League, LeaguePlayer, Matchup, Player, RosterSlot, Team
from app.services import scoring

router = APIRouter(prefix="/api/manual", tags=["manual"])


# --- schemas -----------------------------------------------------------


class LeagueCreate(BaseModel):
    name: str
    season: int
    is_keeper: bool = False
    num_teams: int = 12
    scoring_preset: str | None = None
    scoring_rules: dict | None = None
    roster_slots: dict | None = None


class LeagueUpdate(BaseModel):
    name: str | None = None
    is_keeper: bool | None = None
    num_teams: int | None = None
    scoring_rules: dict | None = None
    roster_slots: dict | None = None


class LeagueManualOut(BaseModel):
    league_key: str
    name: str
    season: int
    is_keeper: bool
    num_teams: int | None = None
    scoring_rules: dict | None = None
    roster_slots: dict | None = None


class TeamCreate(BaseModel):
    name: str
    manager_name: str | None = None
    is_my_team: bool = False


class TeamUpdate(BaseModel):
    name: str | None = None
    manager_name: str | None = None
    is_my_team: bool | None = None


class TeamManualOut(BaseModel):
    id: int
    team_key: str
    name: str
    manager_name: str | None = None
    is_my_team: bool


class RosterPlayerOut(BaseModel):
    player_id: int
    full_name: str
    position: str | None = None
    nfl_team: str | None = None
    injury_status: str | None = None


class RosterAdd(BaseModel):
    player_id: int


# --- helpers -------------------------------------------------------------


def _league_out(league: League) -> LeagueManualOut:
    settings = league.settings_json or {}
    return LeagueManualOut(
        league_key=league.league_key,
        name=league.name,
        season=league.season,
        is_keeper=league.is_keeper,
        num_teams=league.num_teams,
        scoring_rules=settings.get("scoring_rules"),
        roster_slots=settings.get("roster_slots"),
    )


def _get_manual_league(db: Session, league_key: str) -> League:
    league = db.query(League).filter(League.league_key == league_key).one_or_none()
    if league is None:
        raise HTTPException(status_code=404, detail=f"Unknown league {league_key}")
    if league.source != "manual":
        raise HTTPException(status_code=409, detail="League is not a manual league")
    return league


def _get_manual_team(db: Session, team_id: int) -> tuple[Team, League]:
    team = db.query(Team).filter(Team.id == team_id).one_or_none()
    if team is None:
        raise HTTPException(status_code=404, detail=f"Unknown team {team_id}")
    league = db.query(League).filter(League.id == team.league_id).one_or_none()
    if league is None or league.source != "manual":
        raise HTTPException(
            status_code=409, detail="Team does not belong to a manual league"
        )
    return team, league


def _clear_other_my_teams(db: Session, league_id: int, team_id: int) -> None:
    db.query(Team).filter(Team.league_id == league_id, Team.id != team_id).update(
        {"is_my_team": False}
    )


# --- league endpoints ------------------------------------------------------


@router.post("/leagues", response_model=LeagueManualOut, status_code=201)
def create_league(payload: LeagueCreate, db: Session = Depends(get_db)) -> LeagueManualOut:
    if payload.scoring_rules is not None:
        rules = payload.scoring_rules
        scoring_type = "custom"
    else:
        preset_name = payload.scoring_preset or "half_ppr"
        try:
            rules = scoring.get_preset(preset_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        scoring_type = preset_name

    roster_slots = payload.roster_slots or dict(scoring.DEFAULT_ROSTER_SLOTS)

    league = League(
        league_key="",
        game_key="manual",
        name=payload.name,
        season=payload.season,
        is_keeper=payload.is_keeper,
        num_teams=payload.num_teams,
        scoring_type=scoring_type,
        source="manual",
        settings_json={
            "manual": True,
            "scoring_rules": rules,
            "roster_slots": roster_slots,
        },
    )
    db.add(league)
    db.flush()
    league.league_key = f"manual.{league.id}"
    db.commit()
    db.refresh(league)
    return _league_out(league)


@router.put("/leagues/{league_key}", response_model=LeagueManualOut)
def update_league(
    league_key: str, payload: LeagueUpdate, db: Session = Depends(get_db)
) -> LeagueManualOut:
    league = _get_manual_league(db, league_key)
    settings = dict(league.settings_json or {})

    if payload.name is not None:
        league.name = payload.name
    if payload.is_keeper is not None:
        league.is_keeper = payload.is_keeper
    if payload.num_teams is not None:
        league.num_teams = payload.num_teams
    if payload.scoring_rules is not None:
        settings["scoring_rules"] = payload.scoring_rules
        league.scoring_type = "custom"
    if payload.roster_slots is not None:
        settings["roster_slots"] = payload.roster_slots

    settings["manual"] = True
    league.settings_json = settings
    db.commit()
    db.refresh(league)
    return _league_out(league)


@router.delete("/leagues/{league_key}", status_code=204)
def delete_league(league_key: str, db: Session = Depends(get_db)) -> Response:
    league = _get_manual_league(db, league_key)

    team_ids = [t.id for t in db.query(Team.id).filter(Team.league_id == league.id).all()]
    if team_ids:
        db.query(RosterSlot).filter(RosterSlot.team_id.in_(team_ids)).delete(
            synchronize_session=False
        )
    db.query(LeaguePlayer).filter(LeaguePlayer.league_id == league.id).delete(
        synchronize_session=False
    )
    db.query(Matchup).filter(Matchup.league_id == league.id).delete(
        synchronize_session=False
    )
    db.query(Team).filter(Team.league_id == league.id).delete(synchronize_session=False)
    db.delete(league)
    db.commit()
    return Response(status_code=204)


# --- team endpoints ----------------------------------------------------


@router.post(
    "/leagues/{league_key}/teams", response_model=TeamManualOut, status_code=201
)
def create_team(
    league_key: str, payload: TeamCreate, db: Session = Depends(get_db)
) -> Team:
    league = _get_manual_league(db, league_key)

    team = Team(
        team_key="",
        league_id=league.id,
        name=payload.name,
        manager_name=payload.manager_name,
        is_my_team=payload.is_my_team,
    )
    db.add(team)
    db.flush()
    team.team_key = f"manual.{league.id}.t.{team.id}"

    if payload.is_my_team:
        _clear_other_my_teams(db, league.id, team.id)

    db.commit()
    db.refresh(team)
    return team


@router.put("/teams/{team_id}", response_model=TeamManualOut)
def update_team(team_id: int, payload: TeamUpdate, db: Session = Depends(get_db)) -> Team:
    team, league = _get_manual_team(db, team_id)

    if payload.name is not None:
        team.name = payload.name
    if payload.manager_name is not None:
        team.manager_name = payload.manager_name
    if payload.is_my_team is not None:
        team.is_my_team = payload.is_my_team
        if payload.is_my_team:
            _clear_other_my_teams(db, league.id, team.id)

    db.commit()
    db.refresh(team)
    return team


@router.delete("/teams/{team_id}", status_code=204)
def delete_team(team_id: int, db: Session = Depends(get_db)) -> Response:
    team, _league = _get_manual_team(db, team_id)

    db.query(RosterSlot).filter(RosterSlot.team_id == team.id).delete(
        synchronize_session=False
    )
    db.query(LeaguePlayer).filter(LeaguePlayer.on_team_id == team.id).delete(
        synchronize_session=False
    )
    db.delete(team)
    db.commit()
    return Response(status_code=204)


# --- roster endpoints ----------------------------------------------------


@router.get("/teams/{team_id}/roster", response_model=list[RosterPlayerOut])
def get_roster(team_id: int, db: Session = Depends(get_db)) -> list[RosterPlayerOut]:
    team, _league = _get_manual_team(db, team_id)

    rows = (
        db.query(LeaguePlayer, Player)
        .join(Player, Player.id == LeaguePlayer.player_id)
        .filter(LeaguePlayer.on_team_id == team.id)
        .order_by(Player.full_name)
        .all()
    )
    return [
        RosterPlayerOut(
            player_id=player.id,
            full_name=player.full_name,
            position=player.position,
            nfl_team=player.nfl_team,
            injury_status=player.injury_status,
        )
        for _lp, player in rows
    ]


@router.post("/teams/{team_id}/roster", response_model=RosterPlayerOut, status_code=201)
def add_roster_player(
    team_id: int, payload: RosterAdd, db: Session = Depends(get_db)
) -> RosterPlayerOut:
    team, league = _get_manual_team(db, team_id)

    player = db.query(Player).filter(Player.id == payload.player_id).one_or_none()
    if player is None:
        raise HTTPException(status_code=404, detail=f"Unknown player {payload.player_id}")

    existing = (
        db.query(LeaguePlayer)
        .filter(
            LeaguePlayer.league_id == league.id,
            LeaguePlayer.player_id == player.id,
        )
        .one_or_none()
    )
    if existing is not None:
        raise HTTPException(
            status_code=409, detail="Player is already rostered in this league"
        )

    league_player = LeaguePlayer(
        league_id=league.id,
        player_id=player.id,
        status="T",
        on_team_id=team.id,
    )
    db.add(league_player)
    db.commit()

    return RosterPlayerOut(
        player_id=player.id,
        full_name=player.full_name,
        position=player.position,
        nfl_team=player.nfl_team,
        injury_status=player.injury_status,
    )


@router.delete("/teams/{team_id}/roster/{player_id}", status_code=204)
def remove_roster_player(
    team_id: int, player_id: int, db: Session = Depends(get_db)
) -> Response:
    team, league = _get_manual_team(db, team_id)

    league_player = (
        db.query(LeaguePlayer)
        .filter(
            LeaguePlayer.league_id == league.id,
            LeaguePlayer.player_id == player_id,
            LeaguePlayer.on_team_id == team.id,
        )
        .one_or_none()
    )
    if league_player is None:
        raise HTTPException(
            status_code=404, detail="Player is not on this team's roster"
        )

    db.delete(league_player)
    db.commit()
    return Response(status_code=204)
