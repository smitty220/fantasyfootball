"""Shared test fixtures: an isolated on-disk SQLite DB per test."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401  (register models before create_all)
from app.config import settings
from app.db import Base, get_db
from app.main import app as fastapi_app
from app.models import OAuthToken


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session):
    """TestClient wired to the per-test database."""

    def override_get_db():
        yield db_session

    fastapi_app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(fastapi_app) as test_client:
            yield test_client
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def yahoo_credentials(monkeypatch):
    monkeypatch.setattr(settings, "YAHOO_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(settings, "YAHOO_CLIENT_SECRET", "test-client-secret")
    return ("test-client-id", "test-client-secret")


def make_token(
    db_session,
    access_token: str = "access-1",
    refresh_token: str = "refresh-1",
    expires_in_seconds: int = 3600,
) -> OAuthToken:
    now = utcnow()
    token = OAuthToken(
        provider="yahoo",
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=now + timedelta(seconds=expires_in_seconds),
        updated_at=now,
    )
    db_session.add(token)
    db_session.commit()
    db_session.refresh(token)
    return token
