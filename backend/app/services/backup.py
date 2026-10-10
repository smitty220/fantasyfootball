"""SQLite backups into ``<data dir>/backups/``.

Two callers share one implementation:

- app startup (``create_app``) takes one dated copy per day *before*
  migrations touch the DB: ``app-YYYYMMDD.db`` (skipped if it already exists,
  so a same-day restart doesn't overwrite the morning's copy with a
  possibly-worse state);
- the daily ``db_backup`` registry job takes a timestamped copy,
  ``app-YYYYMMDD-HHMMSS.db``.

Copies use sqlite3's online backup API, so they are consistent even while the
app is writing. Retention only ever deletes files matching the app's own
naming pattern, newest ``keep`` survive.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db import engine as _default_engine

KEEP_BACKUPS = 14


def _db_path(bind: Engine) -> Path | None:
    url = str(bind.url)
    if not url.startswith("sqlite:///") or url.startswith("sqlite:///:memory:"):
        return None
    path = Path(url.removeprefix("sqlite:///"))
    if not path.is_file() or path.stat().st_size == 0:
        return None
    return path


def _pattern(stem: str, suffix: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(stem)}-\d{{8}}(-\d{{6}})?{re.escape(suffix)}$")


def backup_sqlite(
    bind: Engine | None = None,
    keep: int = KEEP_BACKUPS,
    *,
    timestamped: bool = False,
    now: datetime | None = None,
) -> Path | None:
    """Back up a file-based SQLite DB; returns the backup path, or None for
    non-SQLite/empty/missing DBs.

    ``timestamped=False`` -> one ``stem-YYYYMMDD.ext`` per day (existing file
    kept); ``True`` -> ``stem-YYYYMMDD-HHMMSS.ext``.
    """
    db_path = _db_path(bind or _default_engine)
    if db_path is None:
        return None
    now = now or datetime.now()

    backups = db_path.parent / "backups"
    backups.mkdir(exist_ok=True)
    fmt = "%Y%m%d-%H%M%S" if timestamped else "%Y%m%d"
    target = backups / f"{db_path.stem}-{now.strftime(fmt)}{db_path.suffix}"

    if not target.exists():
        tmp = target.with_name(target.name + ".tmp")
        tmp.unlink(missing_ok=True)
        src = sqlite3.connect(db_path)
        try:
            dst = sqlite3.connect(tmp)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        tmp.replace(target)

    pattern = _pattern(db_path.stem, db_path.suffix)
    ours = sorted(p for p in backups.iterdir() if pattern.match(p.name))
    for old in ours[: max(len(ours) - keep, 0)]:
        old.unlink()
    return target


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def refresh_backup(db: Session) -> str:
    """Registry job: timestamped consistent backup, logged under ``db_backup``."""
    from app.models import SyncLog

    log = SyncLog(resource="db_backup", started_at=_utcnow(), status="running")
    db.add(log)
    db.commit()
    try:
        target = backup_sqlite(db.get_bind(), timestamped=True)
        log.message = f"Wrote {target.name}" if target else "Skipped: not a file-based SQLite DB"
        log.status = "success"
    except Exception as exc:
        log.status = "error"
        log.message = str(exc)[:2000]
        raise
    finally:
        log.finished_at = _utcnow()
        db.commit()
    return log.message
