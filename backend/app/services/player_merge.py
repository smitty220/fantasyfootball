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

    python -m app.services.player_merge [--only 12736,12745] [--prune] [--apply]

``--prune`` also removes every player no Yahoo pull references (see
:func:`players_outside_yahoo`): Yahoo is the source of truth for which
players exist.
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


def _team(abbr: str | None) -> str:
    """Normalized team, with "FA" (no team) treated as unknown."""
    team = normalize_team_abbr(abbr)
    return "" if team.rstrip("*") == "FA" else team


def _same_entity(dup: Player, candidate: Player) -> bool:
    position = normalize_position(dup.position)
    if not position or normalize_position(candidate.position) != position:
        return False
    dup_team = _team(dup.nfl_team)
    candidate_team = _team(candidate.nfl_team)
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
    has one is some other Yahoo player) that carry some other platform ID --
    an ID-less row has nothing to contribute and only makes names ambiguous.
    """
    candidates = [
        p
        for p in db.query(Player).filter(Player.yahoo_id.is_(None)).all()
        if any(getattr(p, column) for column in _OTHER_IDS)
    ]
    pairs: list[tuple[Player, Player]] = []
    for dup in db.query(Player).filter(Player.yahoo_id.isnot(None)).all():
        if not _is_yahoo_only(dup):
            continue
        matches = [c for c in candidates if _same_entity(dup, c)]
        if len(matches) > 1:
            # The pre-Yahoo sources sometimes split one person into a
            # projection-linked row and a Sleeper-only row; the one carrying
            # projection IDs is the canonical one (the other gets pruned).
            matches = [c for c in matches if c.fantasypros_id or c.espn_id]
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


_CHUNK = 500  # stay well under SQLite's bound-parameter limit


def players_outside_yahoo(db: Session) -> list[Player]:
    """Players no Yahoo pull references: not on a roster, not in any league's
    free-agent/waiver lists, never drafted.

    Yahoo is the source of truth for which players exist; anything else was
    seeded by Sleeper or the nflverse crosswalk before Yahoo access. A
    ``yahoo_id`` alone doesn't count -- the crosswalk fills those in for
    veterans Yahoo never returned to us.
    """
    referenced: set[int] = set()
    for column in (LeaguePlayer.player_id, RosterSlot.player_id, DraftPick.player_id):
        referenced.update(pid for (pid,) in db.query(column).distinct() if pid is not None)
    return [p for p in db.query(Player).all() if p.id not in referenced]


def prune_players(db: Session, players: list[Player]) -> dict[str, int]:
    """Delete ``players`` and their projection/value/trending/actuals rows.

    Returns row counts deleted per table. Flushes but does not commit.
    """
    ids = [p.id for p in players]
    counts: dict[str, int] = {}
    for model in (Projection, TradeValue, TrendingSignal, ActualStat, Player):
        column = model.id if model is Player else model.player_id
        deleted = 0
        for start in range(0, len(ids), _CHUNK):
            deleted += (
                db.query(model)
                .filter(column.in_(ids[start : start + _CHUNK]))
                .delete(synchronize_session=False)
            )
        counts[model.__tablename__] = deleted
    db.flush()
    db.expire_all()
    return counts


def main(argv: list[str] | None = None) -> None:
    from app.db import SessionLocal

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="merge (default: dry run)")
    parser.add_argument(
        "--only", help="comma-separated duplicate player ids to limit the run to"
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="after merging, delete players no Yahoo pull references",
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
        if args.prune:
            # In a dry run the canonical rows of unmerged pairs aren't
            # referenced yet; they would be after --apply, so leave them out.
            pending = {keep.id for _, keep in pairs} if not args.apply else set()
            outside = [p for p in players_outside_yahoo(db) if p.id not in pending]
            ids = {p.id for p in outside}
            linked = sum(1 for p in outside if any(getattr(p, c) for c in _OTHER_IDS))
            projected = {
                pid
                for (pid,) in db.query(Projection.player_id).distinct()
                if pid in ids
            }
            print(
                f"prune: {len(outside)} of {db.query(Player).count()} player(s) are in no "
                f"Yahoo pull ({linked} with platform IDs, {len(projected)} with "
                f"projections); {sum(1 for p in outside if p.yahoo_id)} carry a yahoo_id"
            )
            if args.apply:
                counts = prune_players(db, outside)
                db.commit()
                print(f"pruned: {counts}")
        if not args.apply:
            print("dry run: nothing changed (pass --apply to merge/prune)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
