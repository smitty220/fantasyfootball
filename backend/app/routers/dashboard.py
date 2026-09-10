"""Cross-league home dashboard: one aggregate endpoint over every league.

Reuses :func:`app.services.evaluator.team_lineup` for the per-team alert
computations and :func:`app.services.evaluator.evaluate_free_agents` for the
top-free-agent section, so this module owns no scoring/eligibility logic of
its own beyond the small slot-eligibility helper needed to compare a bench
player against a starter's slot.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import League, Team
from app.services import evaluator
from app.services.yahoo.sync import current_nfl_season

router = APIRouter(prefix="/api", tags=["dashboard"])

SOURCES_DESCRIPTION = (
    "Comma-separated projection sources to use (e.g. \"fantasypros,espn\"). "
    "Several sources are averaged; omit for the default best-source pick."
)


# --- schemas -----------------------------------------------------------


class ByeStarterAlert(BaseModel):
    name: str
    slot: str


class InjuredStarterAlert(BaseModel):
    name: str
    slot: str
    status: str | None = None


class BenchBeatsStarterAlert(BaseModel):
    starter: str
    slot: str
    bench: str
    bench_week_points: float


class DashboardAlerts(BaseModel):
    bye_starters: list[ByeStarterAlert] = Field(default_factory=list)
    injured_starters: list[InjuredStarterAlert] = Field(default_factory=list)
    bench_beats_starter: list[BenchBeatsStarterAlert] = Field(default_factory=list)
    fa_week_flags: int = 0
    fa_ros_flags: int = 0


class DashboardTopFreeAgent(BaseModel):
    name: str
    position: str | None = None
    vor: float
    week_delta: float | None = None
    trending_add: int | None = None


class DashboardTeam(BaseModel):
    id: int
    name: str


class DashboardLeague(BaseModel):
    league_key: str
    name: str
    is_keeper: bool
    week: int | None = None
    my_team: DashboardTeam | None = None
    #: Null (rather than an object of empty fields) when ``my_team`` is null.
    alerts: DashboardAlerts | None = None
    top_free_agents: list[DashboardTopFreeAgent] = Field(default_factory=list)
    my_lineup_week_points: float | None = None


class DashboardResponse(BaseModel):
    leagues: list[DashboardLeague] = Field(default_factory=list)


# --- helpers -------------------------------------------------------------


def _sources(raw: str | None) -> tuple[str, ...] | None:
    """Validated source selection, or a 400 naming the source we do not know."""
    try:
        return evaluator.validate_sources(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _eligible_positions(slot: str) -> frozenset[str]:
    """Positions that may fill ``slot``, mirroring the lineup-builder's rules.

    Direct slots take an exact position match; FLEX/SUPERFLEX expand to
    :data:`evaluator.FLEX_POSITIONS`/:data:`evaluator.SUPERFLEX_POSITIONS`;
    DEF also accepts a ``DST``-labelled player, since some projection sources
    store the position under that spelling.
    """
    if slot == "FLEX":
        return frozenset(evaluator.FLEX_POSITIONS)
    if slot == "SUPERFLEX":
        return frozenset(evaluator.SUPERFLEX_POSITIONS)
    if slot == "DEF":
        return frozenset({"DEF", "DST"})
    return frozenset({slot})


def _compute_alerts(lineup: dict) -> dict:
    """Alert payload from a :func:`evaluator.team_lineup` result."""
    bye_starters: list[dict] = []
    injured_starters: list[dict] = []
    bench_beats_starter: list[dict] = []
    fa_week_flags = 0
    fa_ros_flags = 0

    starters: list[tuple[str, dict]] = []
    for entry in lineup["slots"]:
        player = entry["player"]
        if player is None:
            continue
        slot = entry["slot"]
        starters.append((slot, player))

        if player.get("on_bye"):
            bye_starters.append({"name": player["full_name"], "slot": slot})
        if player.get("injury_status"):
            injured_starters.append(
                {
                    "name": player["full_name"],
                    "slot": slot,
                    "status": player["injury_status"],
                }
            )
        if player.get("better_fa_week_points") is not None:
            fa_week_flags += 1
        if player.get("better_fa_ros_points") is not None:
            fa_ros_flags += 1

    bench = lineup["bench"]
    for player in bench:
        if player.get("better_fa_week_points") is not None:
            fa_week_flags += 1
        if player.get("better_fa_ros_points") is not None:
            fa_ros_flags += 1

    for slot, starter in starters:
        eligible = _eligible_positions(slot)
        starter_points = starter.get("week_points")
        starter_points = starter_points if starter_points is not None else 0.0

        best_bench: dict | None = None
        for candidate in bench:
            candidate_points = candidate.get("week_points")
            if candidate_points is None:
                continue
            if candidate.get("position") not in eligible:
                continue
            if candidate_points <= starter_points:
                continue
            if best_bench is None or candidate_points > best_bench["week_points"]:
                best_bench = candidate

        if best_bench is not None:
            bench_beats_starter.append(
                {
                    "starter": starter["full_name"],
                    "slot": slot,
                    "bench": best_bench["full_name"],
                    "bench_week_points": best_bench["week_points"],
                }
            )

    return {
        "bye_starters": bye_starters,
        "injured_starters": injured_starters,
        "bench_beats_starter": bench_beats_starter,
        "fa_week_flags": fa_week_flags,
        "fa_ros_flags": fa_ros_flags,
    }


def _top_free_agents(db: Session, league: League, season: int, sources) -> list[dict]:
    """Up to three highest-VOR free agents with an actual projection.

    :func:`evaluator.evaluate_free_agents` already sorts its rows by
    ``(has_projection, -vor, name)`` with projected players first, so the
    first three projected rows off a modest ``limit`` are exactly the
    league's top-VOR free agents.
    """
    result = evaluator.evaluate_free_agents(
        db, league, limit=25, season=season, sources=sources
    )
    rows = [row for row in result["rows"] if row["has_projection"]][:3]
    return [
        {
            "name": row["full_name"],
            "position": row["position"],
            "vor": row["vor"],
            "week_delta": row["week_delta"],
            "trending_add": row["trending_add"],
        }
        for row in rows
    ]


def _my_team(db: Session, league: League) -> Team | None:
    return (
        db.query(Team)
        .filter(Team.league_id == league.id, Team.is_my_team.is_(True))
        .first()
    )


# --- endpoint --------------------------------------------------------------


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(
    sources: str | None = Query(default=None, description=SOURCES_DESCRIPTION),
    db: Session = Depends(get_db),
) -> DashboardResponse:
    """Cross-league snapshot: my team, lineup alerts and top free agents."""
    parsed_sources = _sources(sources)
    season = current_nfl_season()

    leagues_out: list[DashboardLeague] = []
    for league in db.query(League).order_by(League.league_key).all():
        week = evaluator.current_projection_week(db, season, parsed_sources)
        team = _my_team(db, league)

        if team is None:
            leagues_out.append(
                DashboardLeague(
                    league_key=league.league_key,
                    name=league.name,
                    is_keeper=league.is_keeper,
                    week=week,
                    my_team=None,
                    alerts=None,
                    top_free_agents=[],
                    my_lineup_week_points=None,
                )
            )
            continue

        lineup = evaluator.team_lineup(
            db, league, team.id, season=season, sources=parsed_sources
        )
        my_lineup_week_points = round(
            sum(
                (entry["player"]["week_points"] or 0.0)
                for entry in lineup["slots"]
                if entry["player"] is not None
            ),
            2,
        )

        leagues_out.append(
            DashboardLeague(
                league_key=league.league_key,
                name=league.name,
                is_keeper=league.is_keeper,
                week=lineup["week"],
                my_team=DashboardTeam(id=team.id, name=team.name),
                alerts=DashboardAlerts(**_compute_alerts(lineup)),
                top_free_agents=_top_free_agents(db, league, season, parsed_sources),
                my_lineup_week_points=my_lineup_week_points,
            )
        )

    return DashboardResponse(leagues=leagues_out)
