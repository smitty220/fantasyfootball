from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ActualStat(Base):
    """What a player *actually* did in one NFL week.

    Loaded from the nflverse weekly player-stats dataset (see
    ``app.services.actuals``) and stored in the same canonical stat vocabulary
    projections use (``app.services.stats.CANONICAL_STATS``), so an actual and
    a projection can be scored by the very same engine under a league's own
    rules -- which is what makes source grading (``app.routers.accuracy``)
    possible at all.

    Deliberately **not** a ``Projection`` row with a magic source name: the
    evaluator blends every ``Projection`` row it finds for a player-week (see
    ``evaluator.SOURCE_PRIORITY`` / ``_blend_points``), so parking actuals in
    that table would leak hindsight into free-agent and trade numbers. A
    separate table cannot be blended by accident.

    ``(player_id, season, week)`` is unique: one line per player per week, and
    a re-ingest of the same week updates in place.
    """

    __tablename__ = "actual_stats"
    __table_args__ = (
        UniqueConstraint("player_id", "season", "week", name="uq_actual_stat"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    season: Mapped[int] = mapped_column(Integer, index=True)
    week: Mapped[int] = mapped_column(Integer)
    stat_json: Mapped[dict] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)
