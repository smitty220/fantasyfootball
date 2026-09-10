"""Data admin: manually trigger the sync services and inspect their history.

Each entry in ``REFRESH_REGISTRY`` maps a source name to the (module, function)
pair that performs it; the import happens at call time so this module never
has to import the sync services (some of which are large/slow to import or
owned by other in-flight work) unless actually asked to run one. ESPN and
FantasyPros are wired in later by the orchestrator - just add entries here.
"""

from __future__ import annotations

import importlib
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import SyncLog

router = APIRouter(prefix="/api/data", tags=["data"])

#: source name -> (module path, function name). Function must accept a single
#: ``db: Session`` argument and write its own SyncLog row (see
#: app.services.crosswalk/sleeper/fantasycalc for the pattern).
REFRESH_REGISTRY: dict[str, tuple[str, str]] = {
    "crosswalk": ("app.services.crosswalk", "refresh_crosswalk"),
    "nfl_schedule": ("app.services.nfl_schedule", "refresh_schedule"),
    "sleeper_players": ("app.services.sleeper", "refresh_players"),
    "sleeper_trending": ("app.services.sleeper", "refresh_trending"),
    "fantasycalc": ("app.services.fantasycalc", "refresh_trade_values"),
    "espn_projections": ("app.services.espn", "refresh_season_projections"),
    "espn_week_projections": ("app.services.espn", "refresh_week_projections"),
    "fantasypros_projections": ("app.services.fantasypros", "refresh_season_projections"),
    "fantasypros_week_projections": ("app.services.fantasypros", "refresh_week_projections"),
}


class SyncLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    resource: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    message: str | None = None


@router.post("/refresh/{source}", response_model=SyncLogOut)
def refresh_source(source: str, db: Session = Depends(get_db)) -> SyncLog:
    entry = REFRESH_REGISTRY.get(source)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Unknown data source {source}")

    module_name, func_name = entry
    module = importlib.import_module(module_name)
    refresh_fn = getattr(module, func_name)
    refresh_fn(db)

    log = (
        db.query(SyncLog)
        .filter(SyncLog.resource == source)
        .order_by(SyncLog.id.desc())
        .first()
    )
    if log is None:  # pragma: no cover - defensive; refresh_fn always writes one
        raise HTTPException(
            status_code=500, detail=f"{source} refresh produced no sync log"
        )
    return log


class ScheduleStatusOut(BaseModel):
    source: str
    scheduled: bool
    next_run_at: datetime | None = None


@router.get("/schedule", response_model=list[ScheduleStatusOut])
def data_schedule() -> list[dict]:
    from app.services.scheduler import schedule_status

    return schedule_status()


@router.get("/status", response_model=list[SyncLogOut])
def data_status(db: Session = Depends(get_db)) -> list[SyncLog]:
    rows = (
        db.query(SyncLog)
        .order_by(SyncLog.started_at.desc(), SyncLog.id.desc())
        .all()
    )

    latest_by_resource: dict[str, SyncLog] = {}
    for row in rows:
        latest_by_resource.setdefault(row.resource, row)

    return sorted(latest_by_resource.values(), key=lambda log: log.resource)
