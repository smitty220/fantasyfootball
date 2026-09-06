from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class League(Base):
    __tablename__ = "leagues"

    id: Mapped[int] = mapped_column(primary_key=True)
    league_key: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    game_key: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(128))
    season: Mapped[int] = mapped_column(Integer)
    is_keeper: Mapped[bool] = mapped_column(Boolean, default=False)
    num_teams: Mapped[int | None] = mapped_column(Integer)
    scoring_type: Mapped[str | None] = mapped_column(String(32))
    current_week: Mapped[int | None] = mapped_column(Integer)
    settings_json: Mapped[dict | None] = mapped_column(JSON)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime)

    teams: Mapped[list["Team"]] = relationship(back_populates="league")


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_key: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    manager_name: Mapped[str | None] = mapped_column(String(128))
    is_my_team: Mapped[bool] = mapped_column(Boolean, default=False)
    wins: Mapped[int | None] = mapped_column(Integer)
    losses: Mapped[int | None] = mapped_column(Integer)
    ties: Mapped[int | None] = mapped_column(Integer)
    rank: Mapped[int | None] = mapped_column(Integer)
    points_for: Mapped[float | None] = mapped_column(Float)
    points_against: Mapped[float | None] = mapped_column(Float)
    logo_url: Mapped[str | None] = mapped_column(String(512))

    league: Mapped[League] = relationship(back_populates="teams")


class RosterSlot(Base):
    __tablename__ = "roster_slots"
    __table_args__ = (
        UniqueConstraint("team_id", "week", "player_id", name="uq_roster_slot"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    week: Mapped[int] = mapped_column(Integer)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    selected_position: Mapped[str | None] = mapped_column(String(8))


class Matchup(Base):
    __tablename__ = "matchups"
    __table_args__ = (
        UniqueConstraint(
            "league_id", "week", "home_team_id", "away_team_id", name="uq_matchup"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"), index=True)
    week: Mapped[int] = mapped_column(Integer)
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    away_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    home_points: Mapped[float | None] = mapped_column(Float)
    away_points: Mapped[float | None] = mapped_column(Float)
    is_playoffs: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str | None] = mapped_column(String(16))


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"), index=True)
    yahoo_transaction_key: Mapped[str] = mapped_column(
        String(64), unique=True, index=True
    )
    type: Mapped[str | None] = mapped_column(String(32))
    timestamp: Mapped[datetime | None] = mapped_column(DateTime)
    data_json: Mapped[dict | None] = mapped_column(JSON)


class DraftPick(Base):
    __tablename__ = "draft_picks"
    __table_args__ = (
        UniqueConstraint("league_id", "round", "pick", name="uq_draft_pick"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"), index=True)
    round: Mapped[int] = mapped_column(Integer)
    pick: Mapped[int] = mapped_column(Integer)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    cost: Mapped[int | None] = mapped_column(Integer)
