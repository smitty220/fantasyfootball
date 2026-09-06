from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class TrendingSignal(Base):
    """Add/drop momentum for a player from an external source (Sleeper)."""

    __tablename__ = "trending_signals"
    __table_args__ = (
        UniqueConstraint("player_id", "source", "kind", name="uq_trending"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    source: Mapped[str] = mapped_column(String(24))  # e.g. "sleeper"
    kind: Mapped[str] = mapped_column(String(8))  # "add" / "drop"
    count: Mapped[int] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)
