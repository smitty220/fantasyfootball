"""Data admin router: manual refresh trigger + sync status."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import importlib

from app.models import SyncLog
from app.routers.data import REFRESH_REGISTRY
from app.services import sleeper


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_refresh_unknown_source_404(client):
    assert client.post("/api/data/refresh/nonsense").status_code == 404


def test_refresh_registry_entries_resolve_to_callables():
    assert "espn_week_projections" in REFRESH_REGISTRY
    assert REFRESH_REGISTRY["nfl_schedule"] == (
        "app.services.nfl_schedule",
        "refresh_schedule",
    )
    for source, (module_name, func_name) in REFRESH_REGISTRY.items():
        module = importlib.import_module(module_name)
        assert callable(getattr(module, func_name)), source


def test_refresh_known_source_runs_and_returns_sync_log(client, db_session, monkeypatch):
    def fake_refresh_players(db):
        log = SyncLog(
            resource="sleeper_players",
            started_at=_utcnow(),
            finished_at=_utcnow(),
            status="success",
            message="3 created, 1 updated",
        )
        db.add(log)
        db.commit()
        return log.message

    monkeypatch.setattr(sleeper, "refresh_players", fake_refresh_players)

    response = client.post("/api/data/refresh/sleeper_players")
    assert response.status_code == 200
    body = response.json()
    assert body["resource"] == "sleeper_players"
    assert body["status"] == "success"
    assert body["message"] == "3 created, 1 updated"
    assert body["finished_at"] is not None


def test_status_returns_latest_per_resource(client, db_session):
    now = _utcnow()
    db_session.add_all(
        [
            SyncLog(
                resource="crosswalk",
                started_at=now - timedelta(hours=2),
                finished_at=now - timedelta(hours=2),
                status="success",
                message="old crosswalk run",
            ),
            SyncLog(
                resource="crosswalk",
                started_at=now,
                finished_at=now,
                status="success",
                message="latest crosswalk run",
            ),
            SyncLog(
                resource="fantasycalc",
                started_at=now - timedelta(hours=1),
                finished_at=None,
                status="error",
                message="boom",
            ),
        ]
    )
    db_session.commit()

    body = client.get("/api/data/status").json()
    by_resource = {row["resource"]: row for row in body}

    assert by_resource["crosswalk"]["message"] == "latest crosswalk run"
    assert by_resource["fantasycalc"]["status"] == "error"
    assert by_resource["fantasycalc"]["finished_at"] is None
    assert len(by_resource) == 2


def test_status_empty(client):
    assert client.get("/api/data/status").json() == []
