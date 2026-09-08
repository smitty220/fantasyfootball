"""Programmatic Alembic driver used at app startup instead of a bare
``Base.metadata.create_all()`` call.

Three cases, distinguished by what's already in the target DB:

- Fresh/empty DB (no tables at all) -> ``upgrade`` to head, which runs every
  migration from scratch and creates the full schema.
- Existing pre-Alembic DB (has a ``leagues`` table -- i.e. was created by the
  old ``create_all()`` -- but no ``alembic_version`` table yet): the schema
  is already current, so ``stamp`` it at head rather than replaying
  create-table migrations against tables that already exist.
- Anything else (already has ``alembic_version``): ``upgrade`` to head as
  normal -- a no-op if already current, or applies any migrations newer
  than what was last stamped/applied.

Uses the Alembic Python API (``alembic.command``) against a ``Config`` built
in code, not a subprocess.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from app.db import engine as _default_engine

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"
_ALEMBIC_SCRIPT_LOCATION = _BACKEND_DIR / "alembic"


def _alembic_config(bind: Engine) -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_ALEMBIC_SCRIPT_LOCATION))
    # '%' has special meaning to ConfigParser's interpolation; escape any
    # that show up in the URL (e.g. a password with a literal '%').
    cfg.set_main_option("sqlalchemy.url", str(bind.url).replace("%", "%%"))
    return cfg


def backup_sqlite(bind: Engine | None = None, keep: int = 7) -> Path | None:
    """Copy a file-based SQLite DB to data-dir ``backups/`` before touching it.

    One dated backup per day (a same-day restart doesn't overwrite the
    morning's copy with a possibly-worse state); the newest ``keep`` are
    retained. No-op for non-SQLite/empty/missing DBs.
    """
    import shutil
    from datetime import date

    bind = bind or _default_engine
    url = str(bind.url)
    if not url.startswith("sqlite:///"):
        return None
    db_path = Path(url.removeprefix("sqlite:///"))
    if not db_path.is_file() or db_path.stat().st_size == 0:
        return None

    backups = db_path.parent / "backups"
    backups.mkdir(exist_ok=True)
    target = backups / f"{db_path.stem}-{date.today():%Y%m%d}{db_path.suffix}"
    if not target.exists():
        shutil.copy2(db_path, target)

    existing = sorted(backups.glob(f"{db_path.stem}-*{db_path.suffix}"))
    for old in existing[:-keep]:
        old.unlink()
    return target


def run_migrations(bind: Engine | None = None) -> None:
    """Bring ``bind`` (default: the app's configured engine) up to head."""
    bind = bind or _default_engine
    cfg = _alembic_config(bind)

    with bind.connect() as connection:
        tables = set(inspect(connection).get_table_names())

    if "alembic_version" not in tables and "leagues" in tables:
        command.stamp(cfg, "head")
    else:
        command.upgrade(cfg, "head")
