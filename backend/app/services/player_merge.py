"""Fold duplicate ``players`` rows into their canonical row.

Before the Yahoo sync learned the name fallback in
:func:`app.services.yahoo.sync._get_or_create_player`, every Yahoo player the
nflverse crosswalk had no ``yahoo_id`` for (in practice: recent rookies and
team defenses) got a fresh, otherwise-empty ``players`` row. Those rows carry
the league data (rosters, availability, draft picks) while the canonical row
carries projections and trade values, so the player reads as unprojected.

:func:`find_yahoo_duplicates` pairs each such Yahoo-only row with the single
unambiguous canonical row; :func:`merge_player` repoints everything at the
canonical row and deletes the duplicate. Run as a module for a dry run, or
with ``--apply``::

    python -m app.services.player_merge [--only 12736,12745] [--apply]
"""

from __future__ import annotations

import argparse
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    ActualStat,
    DraftPick,
    LeaguePlayer,
    Player,
    Projection,
    RosterSlot,
    TradeValue,
    TrendingSignal,
)
from app.services.matching import (
    normalize_name,
    normalize_position,
    normalize_team_abbr,
)

#: Player-keyed tables: (model, the other columns of its unique key, whether
#: the duplicate's row wins a clash). The duplicate's league rows are the
#: fresh Yahoo truth; for everything else the canonical row's data is the
#: real one.
_KEYED_TABLES: tuple[tuple[Any, tuple[str, ...], bool], ...] = (
    (LeaguePlayer, ("league_id",), True),
    (RosterSlot, ("team_id", "week"), True),
    (Projection, ("source", "season", "week"), False),
    (TradeValue, ("source", "format"), False),
    (TrendingSignal, ("source", "kind"), False),
    (ActualStat, ("season", "week"), False),
)

_OTHER_IDS = ("sleeper_id", "espn_id", "fantasypros_id", "gsis_id")


def _is_yahoo_only(player: Player) -> bool:
    return player.yahoo_id is not None and all(
        getattr(player, column) is None for column in _OTHER_IDS
    )


def _same_entity(dup: Player, candidate: Player) -> bool:
    position = normalize_position(dup.position)
    if not position or normalize_position(candidate.position) != position:
        return False
    dup_team = normalize_team_abbr(dup.nfl_team)
    candidate_team = normalize_team_abbr(candidate.nfl_team)
    if position == "DST":
        # Defenses have no shared name across sources ("Saints" vs "New
        # Orleans Saints"); the team is the identity.
        return bool(dup_team) and dup_team == candidate_team
    if normalize_name(dup.full_name) != normalize_name(candidate.full_name):
        return False
    # A team on both sides must agree; a missing one doesn't veto.
    return not (dup_team and candidate_team and dup_team != candidate_team)


def find_yahoo_duplicates(db: Session) -> list[tuple[Player, Player]]:
    """``(duplicate, canonical)`` pairs, only where exactly one canonical fits.

    Canonical candidates are rows without a ``yahoo_id`` (a row that already
    has one is some other Yahoo player).
    """
    candidates = db.query(Player).filter(Player.yahoo_id.is_(None)).all()
    pairs: list[tuple[Player, Player]] = []
    for dup in db.query(Player).filter(Player.yahoo_id.isnot(None)).all():
        if not _is_yahoo_only(dup):
            continue
        matches = [c for c in candidates if _same_entity(dup, c)]
        if len(matches) == 1:
            pairs.append((dup, matches[0]))
    return pairs


def _key_filter(model: Any, row: Any, columns: tuple[str, ...]) -> list:
    clauses = []
    for column in columns:
        value = getattr(row, column)
        attr = getattr(model, column)
        clauses.append(attr.is_(None) if value is None else attr == value)
    return clauses


def merge_player(db: Session, dup: Player, keep: Player) -> None:
    """Repoint every reference from ``dup`` to ``keep``, then delete ``dup``.

    ``keep`` inherits the Yahoo ID. Flushes but does not commit.
    """
    for model, columns, dup_wins in _KEYED_TABLES:
        for row in db.query(model).filter(model.player_id == dup.id).all():
            clash = (
                db.query(model)
                .filter(model.player_id == keep.id, *_key_filter(model, row, columns))
                .one_or_none()
            )
            if clash is None:
                row.player_id = keep.id
            elif dup_wins:
                db.delete(clash)
                db.flush()
                row.player_id = keep.id
            else:
                db.delete(row)
        db.flush()

    db.query(DraftPick).filter(DraftPick.player_id == dup.id).update(
        {"player_id": keep.id}, synchronize_session=False
    )

    yahoo_id = dup.yahoo_id
    db.delete(dup)
    db.flush()
    keep.yahoo_id = yahoo_id
    db.flush()


def main(argv: list[str] | None = None) -> None:
    from app.db import SessionLocal

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="merge (default: dry run)")
    parser.add_argument(
        "--only", help="comma-separated duplicate player ids to limit the run to"
    )
    args = parser.parse_args(argv)
    only = {int(x) for x in args.only.split(",")} if args.only else None

    db = SessionLocal()
    try:
        pairs = [
            (dup, keep)
            for dup, keep in find_yahoo_duplicates(db)
            if only is None or dup.id in only
        ]
        for dup, keep in pairs:
            print(
                f"{dup.id:>6} {dup.full_name!r} {dup.position} {dup.nfl_team}"
                f"  ->  {keep.id:>6} {keep.full_name!r} {keep.position} {keep.nfl_team}"
            )
        if only is not None:
            missing = sorted(only - {dup.id for dup, _ in pairs})
            if missing:
                print(f"no unambiguous match for: {missing}")
        print(f"{len(pairs)} pair(s)")
        if args.apply:
            for dup, keep in pairs:
                merge_player(db, dup, keep)
            db.commit()
            print("merged")
        else:
            print("dry run: nothing changed (pass --apply to merge)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
