"""Background auto-refresh scheduler.

The module-level ``scheduler`` singleton in app.services.scheduler is
replaced with a fresh ``BackgroundScheduler`` per test (via the
``fresh_scheduler`` fixture below) so tests never share state with each
other or spin up real jobs against the dev database. The suite-wide
``_disable_scheduler`` autouse fixture (tests/conftest.py) keeps
SCHEDULER_ENABLED False by default; tests that want it on set it explicitly.
"""

from __future__ import annotations

import logging

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

import app.services.scheduler as scheduler_module
from app.config import settings
from app.routers.data import REFRESH_REGISTRY
from app.services.fantasypros import FantasyProsNotConfiguredError
from app.services.scheduler import (
    _job_id,
    _run_refresh,
    schedule_status,
    shutdown_scheduler,
    start_scheduler,
)


@pytest.fixture()
def fresh_scheduler(monkeypatch):
    """Swap in a brand-new BackgroundScheduler and reset the "started" flag,
    so each test gets an isolated scheduler instance regardless of what
    other tests in this file did."""
    new_scheduler = BackgroundScheduler()
    monkeypatch.setattr(scheduler_module, "scheduler", new_scheduler)
    monkeypatch.setattr(scheduler_module, "_started", False)
    yield new_scheduler
    if new_scheduler.running:
        new_scheduler.shutdown(wait=False)


def test_start_scheduler_registers_every_registry_source(fresh_scheduler, monkeypatch):
    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", True)

    start_scheduler()

    assert fresh_scheduler.running is True
    for source in REFRESH_REGISTRY:
        job = fresh_scheduler.get_job(_job_id(source))
        assert job is not None, f"no job scheduled for {source}"


def test_nfl_schedule_runs_weekly(fresh_scheduler, monkeypatch):
    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", True)

    start_scheduler()

    trigger = fresh_scheduler.get_job(_job_id("nfl_schedule")).trigger
    fields = {field.name: str(field) for field in trigger.fields}
    assert fields["day_of_week"] == "tue"
    assert (fields["hour"], fields["minute"]) == ("3", "0")


def test_start_scheduler_is_idempotent(fresh_scheduler, monkeypatch):
    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", True)

    start_scheduler()
    start_scheduler()
    start_scheduler()

    assert len(fresh_scheduler.get_jobs()) == len(REFRESH_REGISTRY)


def test_scheduler_enabled_false_registers_no_jobs(fresh_scheduler, monkeypatch):
    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", False)

    start_scheduler()

    assert fresh_scheduler.get_jobs() == []
    assert fresh_scheduler.running is False


def test_shutdown_is_safe_when_never_started(fresh_scheduler, monkeypatch):
    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", False)

    start_scheduler()
    shutdown_scheduler()  # must not raise

    assert fresh_scheduler.running is False


def test_shutdown_then_start_again_rebuilds_jobs(fresh_scheduler, monkeypatch):
    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", True)

    start_scheduler()
    shutdown_scheduler()
    assert fresh_scheduler.running is False

    start_scheduler()
    assert fresh_scheduler.running is True
    assert len(fresh_scheduler.get_jobs()) == len(REFRESH_REGISTRY)


def test_schedule_status_shape_when_scheduled(fresh_scheduler, monkeypatch):
    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", True)
    start_scheduler()

    rows = schedule_status()

    assert {row["source"] for row in rows} == set(REFRESH_REGISTRY)
    for row in rows:
        assert set(row.keys()) == {"source", "scheduled", "next_run_at"}
        assert row["scheduled"] is True
        assert isinstance(row["next_run_at"], str)
        # ISO-parseable
        from datetime import datetime

        datetime.fromisoformat(row["next_run_at"])


def test_schedule_status_shape_when_disabled(fresh_scheduler, monkeypatch):
    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", False)
    start_scheduler()  # no-op: nothing registered

    rows = schedule_status()

    assert {row["source"] for row in rows} == set(REFRESH_REGISTRY)
    for row in rows:
        assert row["scheduled"] is False
        assert row["next_run_at"] is None


def test_run_refresh_calls_registry_function(monkeypatch):
    calls = []

    def fake_refresh_players(db):
        calls.append(db)
        return "ok"

    import app.services.sleeper as sleeper

    monkeypatch.setattr(sleeper, "refresh_players", fake_refresh_players)

    _run_refresh("sleeper_players")

    assert len(calls) == 1


def test_run_refresh_swallows_fantasypros_not_configured(monkeypatch, caplog):
    import app.services.fantasypros as fantasypros

    def raise_not_configured(db):
        raise FantasyProsNotConfiguredError("no key")

    monkeypatch.setattr(fantasypros, "refresh_season_projections", raise_not_configured)

    with caplog.at_level(logging.INFO, logger=scheduler_module.__name__):
        _run_refresh("fantasypros_projections")  # must not raise

    assert any(
        "fantasypros_projections" in record.getMessage() and "skipping" in record.getMessage()
        for record in caplog.records
    )
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


def test_run_refresh_logs_and_swallows_unexpected_errors(monkeypatch, caplog):
    import app.services.sleeper as sleeper

    def boom(db):
        raise RuntimeError("api is down")

    monkeypatch.setattr(sleeper, "refresh_players", boom)

    with caplog.at_level(logging.ERROR, logger=scheduler_module.__name__):
        _run_refresh("sleeper_players")  # must not raise

    assert any(record.levelno >= logging.ERROR for record in caplog.records)


def test_data_schedule_endpoint(client, monkeypatch):
    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", False)

    response = client.get("/api/data/schedule")

    assert response.status_code == 200
    body = response.json()
    assert {row["source"] for row in body} == set(REFRESH_REGISTRY)
    for row in body:
        assert row["scheduled"] is False
        assert row["next_run_at"] is None
