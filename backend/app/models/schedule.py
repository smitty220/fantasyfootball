from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class NflGame(Base):
    """One scheduled NFL regular-season game.

    Loaded from the nflverse games/schedules dataset (see
    ``app.services.nfl_schedule``). Team abbreviations are stored in Sleeper's
    convention -- the one the bulk of our ``Player.nfl_team`` values already
    use -- so a team can be joined to its players without a translation step.

    ``(season, week, home_team)`` is unique: a team hosts at most one game per
    week, which makes the home side a natural upsert key.
    """

    __tablename__ = "nfl_games"
    __table_args__ = (
        UniqueConstraint("season", "week", "home_team", name="uq_nfl_game"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int] = mapped_column(Integer, index=True)
    week: Mapped[int] = mapped_column(Integer)
    home_team: Mapped[str] = mapped_column(String(8))
    away_team: Mapped[str] = mapped_column(String(8))
    kickoff: Mapped[datetime | None] = mapped_column(DateTime)
