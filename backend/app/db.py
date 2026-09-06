from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


def _ensure_sqlite_data_dir(database_url: str) -> None:
    """If the configured database is a file-based SQLite DB, make sure its
    parent directory exists so the engine doesn't fail on first connect."""
    if not database_url.startswith("sqlite:///"):
        return

    raw_path = database_url.removeprefix("sqlite:///")
    if raw_path in ("", ":memory:"):
        return

    db_path = Path(raw_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_data_dir(settings.DATABASE_URL)

connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
