"""Projection-source accuracy: how wrong was each source, in *this* league?

``GET /api/leagues/{league_key}/accuracy`` grades every projection source in
``evaluator.SOURCE_PRIORITY`` against the actual weekly stat lines
``app.services.actuals`` ingests.

Grading definition
------------------
A **graded player-week** is a ``(player, week)`` for which we hold *both* an
:class:`~app.models.ActualStat` row and that source's *weekly*
:class:`~app.models.Projection` row (``week = N``; the ``week IS NULL``
season/ROS rows are a different horizon and are never graded here). For each
one::

    error = projected_league_points - actual_league_points

where both sides are the same stat line scored by
``scoring.score_stat_line(..., games=1)`` under **this league's** rules. That
is the point of doing it per league rather than once globally: a source that
overshoots receptions looks materially worse in a PPR league than in a
standard one, and the number a manager cares about is the one in their own
scoring.

From the errors of a set of player-weeks:

* ``mae``  -- mean absolute error, i.e. average how-far-off, in points.
* ``bias`` -- mean *signed* error. Positive means the source **over**projects.

reported ``overall`` (every graded player-week), ``by_week``, and
``by_position``. ``n`` is the count of graded player-weeks behind each figure;
a source with nothing to grade reports ``n = 0`` and zeros rather than being
omitted, so the shape of the response never depends on the data.

Players with no position on their row are skipped (there is no meaningful
bucket for them), as are positions that cannot start in a fantasy lineup.
Team defenses grade only if DST actuals ever land -- today they don't, see
``app.services.actuals``.

Everything is computed from three queries (actuals, projections, players) and
a single pass per source: no per-player queries.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ActualStat, League, Player, Projection
from app.services import scoring
from app.services.evaluator import SOURCE_PRIORITY
from app.services.yahoo.sync import current_nfl_season

router = APIRouter(prefix="/api/leagues", tags=["accuracy"])

#: Positions we grade, in the order they are reported. Anything else (IDP,
#: punters, a blank position) is skipped.
GRADED_POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE", "K", "DEF")

#: Position spellings sources/crosswalks use, folded onto GRADED_POSITIONS.
_POSITION_ALIASES = {"PK": "K", "DST": "DEF", "D/ST": "DEF", "FB": "RB"}


class AccuracyMetrics(BaseModel):
    n: int
    mae: float
    bias: float


class WeekAccuracy(AccuracyMetrics):
    week: int


class PositionAccuracy(AccuracyMetrics):
    position: str


class SourceAccuracy(BaseModel):
    source: str
    overall: AccuracyMetrics
    by_week: list[WeekAccuracy]
    by_position: list[PositionAccuracy]


class AccuracyResponse(BaseModel):
    league_key: str
    season: int
    #: Weeks with actuals on file, ascending.
    weeks: list[int]
    sources: list[SourceAccuracy]


def normalize_position(position: str | None) -> str | None:
    """A graded position label, or ``None`` for "don't grade this player"."""
    if not position:
        return None
    folded = position.strip().upper()
    folded = _POSITION_ALIASES.get(folded, folded)
    return folded if folded in GRADED_POSITIONS else None


def _metrics(errors: list[float]) -> dict:
    if not errors:
        return {"n": 0, "mae": 0.0, "bias": 0.0}
    total = len(errors)
    return {
        "n": total,
        "mae": round(sum(abs(error) for error in errors) / total, 2),
        "bias": round(sum(errors) / total, 2),
    }


def source_accuracy(
    db: Session, league: League, season: int
) -> tuple[list[int], list[dict]]:
    """``(weeks, per-source accuracy dicts)`` for one league and season."""
    rules = scoring.league_rules(league.settings_json)

    positions = {
        player_id: normalize_position(position)
        for player_id, position in db.query(Player.id, Player.position)
    }

    #: (player_id, week) -> actual points under this league's rules. Scored
    #: once here rather than per source.
    actual_points: dict[tuple[int, int], float] = {}
    weeks: set[int] = set()
    for player_id, week, stat_json in db.query(
        ActualStat.player_id, ActualStat.week, ActualStat.stat_json
    ).filter(ActualStat.season == season):
        weeks.add(week)
        if positions.get(player_id) is None:
            continue
        actual_points[(player_id, week)] = scoring.score_stat_line(
            stat_json or {}, rules, games=1
        )

    errors: dict[str, list[float]] = {source: [] for source in SOURCE_PRIORITY}
    by_week: dict[str, dict[int, list[float]]] = {s: {} for s in SOURCE_PRIORITY}
    by_position: dict[str, dict[str, list[float]]] = {s: {} for s in SOURCE_PRIORITY}

    if actual_points:
        rows = db.query(
            Projection.player_id,
            Projection.source,
            Projection.week,
            Projection.stat_json,
        ).filter(
            Projection.season == season,
            Projection.week.isnot(None),
            Projection.source.in_(SOURCE_PRIORITY),
        )
        for player_id, source, week, stat_json in rows:
            actual = actual_points.get((player_id, week))
            if actual is None or source not in errors:
                continue
            projected = scoring.score_stat_line(stat_json or {}, rules, games=1)
            error = projected - actual
            errors[source].append(error)
            by_week[source].setdefault(week, []).append(error)
            position = positions[player_id]
            by_position[source].setdefault(position, []).append(error)

    sources = []
    for source in SOURCE_PRIORITY:
        weeks_out = [
            {"week": week, **_metrics(by_week[source][week])}
            for week in sorted(by_week[source])
        ]
        positions_out = [
            {"position": position, **_metrics(by_position[source][position])}
            for position in GRADED_POSITIONS
            if position in by_position[source]
        ]
        sources.append(
            {
                "source": source,
                "overall": _metrics(errors[source]),
                "by_week": weeks_out,
                "by_position": positions_out,
            }
        )
    return sorted(weeks), sources


@router.get("/{league_key}/accuracy", response_model=AccuracyResponse)
def league_accuracy(
    league_key: str, db: Session = Depends(get_db)
) -> AccuracyResponse:
    league = db.query(League).filter(League.league_key == league_key).one_or_none()
    if league is None:
        raise HTTPException(status_code=404, detail=f"Unknown league {league_key}")

    season = current_nfl_season()
    weeks, sources = source_accuracy(db, league, season)
    return AccuracyResponse(
        league_key=league.league_key,
        season=season,
        weeks=weeks,
        sources=sources,
    )
