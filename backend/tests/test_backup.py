"""SQLite backups: online-backup copy, naming and retention."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from sqlalchemy import create_engine

from app.models import SyncLog
from app.services.backup import backup_sqlite, refresh_backup


def _make_db(tmp_path):
    path = tmp_path / "app.db"
    con = sqlite3.connect(path)
    con.execute("create table t (x integer)")
    con.execute("insert into t values (42)")
    con.commit()
    con.close()
    return path, create_engine(f"sqlite:///{path}")


def test_startup_backup_is_one_per_day_and_readable(tmp_path):
    path, engine = _make_db(tmp_path)
    now = datetime(2026, 10, 10, 9, 0, 0)

    target = backup_sqlite(engine, now=now)
    assert target.name == "app-20261010.db"
    assert sqlite3.connect(target).execute("select x from t").fetchone() == (42,)

    sqlite3.connect(path).execute("delete from t").connection.commit()
    again = backup_sqlite(engine, now=now)
    assert again == target
    assert sqlite3.connect(target).execute("select count(*) from t").fetchone() == (1,)


def test_timestamped_backup_name(tmp_path):
    _, engine = _make_db(tmp_path)
    target = backup_sqlite(engine, timestamped=True, now=datetime(2026, 10, 10, 4, 30, 5))
    assert target.name == "app-20261010-043005.db"


def test_retention_keeps_newest_and_ignores_foreign_files(tmp_path):
    _, engine = _make_db(tmp_path)
    backups = tmp_path / "backups"
    backups.mkdir()
    for day in range(1, 11):
        (backups / f"app-202610{day:02d}.db").write_bytes(b"x")
    (backups / "app-notes.db").write_bytes(b"x")
    (backups / "other-20200101.db").write_bytes(b"x")
    (backups / "app-20200101.db.bak").write_bytes(b"x")

    backup_sqlite(engine, keep=3, timestamped=True, now=datetime(2026, 10, 20, 4, 30, 0))

    names = sorted(p.name for p in backups.iterdir())
    assert names == [
        "app-20200101.db.bak",
        "app-20261009.db",
        "app-20261010.db",
        "app-20261020-043000.db",
        "app-notes.db",
        "other-20200101.db",
    ]


def test_non_sqlite_or_missing_db_is_noop(tmp_path):
    assert backup_sqlite(create_engine(f"sqlite:///{tmp_path / 'missing.db'}")) is None


def test_refresh_backup_writes_sync_log(tmp_path):
    from sqlalchemy.orm import sessionmaker

    from app.db import Base

    path = tmp_path / "app.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        message = refresh_backup(db)
        log = db.query(SyncLog).filter_by(resource="db_backup").one()
        assert log.status == "success"
        assert log.message == message
        assert len(list((tmp_path / "backups").glob("app-*-*.db"))) == 1
    finally:
        db.close()
