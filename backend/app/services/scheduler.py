"""Background auto-refresh scheduler.

Wraps ``app.routers.data.REFRESH_REGISTRY`` in an APScheduler
``BackgroundScheduler``: each registry entry gets its own job that opens a
fresh ``SessionLocal`` session, runs the same (module, function) target the
manual "Refresh" button calls, and closes the session when done. The sync
services already write their own ``SyncLog`` rows; jobs additionally
``logger.exception`` on failure so a broken source doesn't fail silently in
server logs.

The scheduler object itself is a module-level singleton so ``start_scheduler``
/ ``shutdown_scheduler`` can be called repeatedly (FastAPI lifespan under
uvicorn --reload, a test suite that builds the app many times) without
double-scheduling jobs or crashing on a second shutdown.
"""

from __future__ import annotations

import importlib
import logging
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.db import SessionLocal
from app.routers.data import REFRESH_REGISTRY
from app.services.fantasypros import FantasyProsNotConfiguredError

logger = logging.getLogger(__name__)

#: Generous grace period: if the process was asleep/busy past a job's fire
#: time, still run it up to an hour late rather than skip it silently.
MISFIRE_GRACE_TIME = 60 * 60

#: source -> trigger factory. Interval jobs get a small future ``start_date``
#: offset (rather than all starting "now") so they don't all fire together on
#: process boot; cron jobs are naturally staggered by their hour/minute.
_JOB_SPECS: dict[str, Callable[[], object]] = {
    "sleeper_trending": lambda: IntervalTrigger(hours=6, start_date=_offset(minutes=5)),
    "sleeper_players": lambda: CronTrigger(hour=1, minute=0),
    "fantasycalc": lambda: CronTrigger(hour=1, minute=15),
    "espn_projections": lambda: IntervalTrigger(hours=6, start_date=_offset(minutes=10)),
    "espn_week_projections": lambda: IntervalTrigger(hours=6, start_date=_offset(minutes=20)),
    "fantasypros_projections": lambda: IntervalTrigger(hours=1, start_date=_offset(minutes=30)),
    "fantasypros_week_projections": lambda: IntervalTrigger(hours=1, start_date=_offset(minutes=40)),
    "crosswalk": lambda: CronTrigger(day_of_week="sun", hour=2, minute=0),
}


def _offset(**kwargs):
    """Return a start_date a few minutes/hours in the future, staggering job
    start times so they don't all fire on process boot at once."""
    from datetime import datetime, timedelta

    return datetime.now() + timedelta(**kwargs)


scheduler = BackgroundScheduler()

_started = False


def _run_refresh(source: str) -> None:
    """Job body: run one registry entry's refresh function with its own DB
    session, logging (but not re-raising) any failure so APScheduler keeps
    the job scheduled for next time."""
    module_name, func_name = REFRESH_REGISTRY[source]

    db = SessionLocal()
    try:
        module = importlib.import_module(module_name)
        refresh_fn = getattr(module, func_name)
        refresh_fn(db)
    except FantasyProsNotConfiguredError:
        logger.info(
            "scheduler: skipping %s refresh, FantasyPros is not configured", source
        )
    except Exception:
        logger.exception("scheduler: %s refresh job failed", source)
    finally:
        db.close()


def _job_id(source: str) -> str:
    return f"refresh:{source}"


def start_scheduler() -> None:
    """Register a job per REFRESH_REGISTRY source and start the scheduler.

    Idempotent and safe to call multiple times (uvicorn --reload restarts the
    lifespan, and the test suite builds the FastAPI app repeatedly): a
    no-op if already started, or if ``SCHEDULER_ENABLED`` is false.
    """
    global _started

    if not settings.SCHEDULER_ENABLED:
        return
    if _started and scheduler.running:
        return

    for source in REFRESH_REGISTRY:
        trigger_factory = _JOB_SPECS[source]
        job_id = _job_id(source)
        if scheduler.get_job(job_id) is not None:
            continue
        scheduler.add_job(
            _run_refresh,
            trigger=trigger_factory(),
            args=[source],
            id=job_id,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=MISFIRE_GRACE_TIME,
            replace_existing=True,
        )

    if not scheduler.running:
        scheduler.start()
    _started = True


def shutdown_scheduler() -> None:
    """Stop the scheduler if it's running. Safe to call even if it was never
    started (e.g. SCHEDULER_ENABLED=false, or called twice)."""
    global _started

    if scheduler.running:
        scheduler.shutdown(wait=False)
    _started = False


def schedule_status() -> list[dict]:
    """Per-registry-source scheduling info for the /api/data/schedule endpoint."""
    rows: list[dict] = []
    for source in sorted(REFRESH_REGISTRY):
        job = scheduler.get_job(_job_id(source))
        next_run_at = None
        if job is not None and job.next_run_time is not None:
            next_run_at = job.next_run_time.isoformat()
        rows.append(
            {
                "source": source,
                "scheduled": job is not None,
                "next_run_at": next_run_at,
            }
        )
    return rows
