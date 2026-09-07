from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Player(Base):
    """Canonical player row; per-platform IDs come from the nflverse crosswalk."""

    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(128), index=True)
    position: Mapped[str | None] = mapped_column(String(8), index=True)
    nfl_team: Mapped[str | None] = mapped_column(String(8))
    injury_status: Mapped[str | None] = mapped_column(String(16))
    bye_week: Mapped[int | None] = mapped_column(Integer)
    yahoo_id: Mapped[str | None] = mapped_column(String(16), index=True)
    sleeper_id: Mapped[str | None] = mapped_column(String(16), index=True)
    espn_id: Mapped[str | None] = mapped_column(String(16), index=True)
    fantasypros_id: Mapped[str | None] = mapped_column(String(16), index=True)
    gsis_id: Mapped[str | None] = mapped_column(String(16), index=True)
    # Platform-wide roster/start rates (currently sourced from ESPN).
    percent_owned: Mapped[float | None] = mapped_column(Float)
    percent_started: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)


class LeaguePlayer(Base):
    """Per-league ownership state; FA/W rows are the free-agent list."""

    __tablename__ = "league_players"
    __table_args__ = (
        UniqueConstraint("league_id", "player_id", name="uq_league_player"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"), index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    status: Mapped[str] = mapped_column(String(4))  # FA / W / T / K
    percent_owned: Mapped[float | None] = mapped_column(Float)
    on_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime)


class Projection(Base):
    """Raw projected stat line from one source; week NULL means rest-of-season."""

    __tablename__ = "projections"
    __table_args__ = (
        UniqueConstraint(
            "player_id", "source", "season", "week", name="uq_projection"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    source: Mapped[str] = mapped_column(String(24), index=True)
    season: Mapped[int] = mapped_column(Integer)
    week: Mapped[int | None] = mapped_column(Integer)
    stat_json: Mapped[dict] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)


class TradeValue(Base):
    __tablename__ = "trade_values"
    __table_args__ = (
        UniqueConstraint("player_id", "source", "format", name="uq_trade_value"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    source: Mapped[str] = mapped_column(String(24))
    format: Mapped[str] = mapped_column(String(16))  # redraft / dynasty
    value: Mapped[float] = mapped_column(Float)
    trend_30d: Mapped[float | None] = mapped_column(Float)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)
