"""Sleeper's free public API: player metadata + add/drop trending.

Base URL: https://api.sleeper.app/v1 (no auth required). Response shapes were
confirmed live on 2026-09-06:

- ``GET /players/nfl`` returns a dict keyed by Sleeper player id (a string;
  for team defenses it's the team abbreviation, e.g. ``"HOU"``). Fields used
  here: ``position`` (already ``"QB"/"RB"/"WR"/"TE"/"K"/"DEF"``, not "PK"),
  ``full_name`` (absent for DEF entries; built from first/last name instead),
  ``team``, ``injury_status``, ``yahoo_id`` (int), ``espn_id`` (int),
  ``gsis_id`` (str).
- ``GET /players/nfl/trending/{add,drop}?lookback_hours=24&limit=100`` returns
  a bare list of ``{"count": int, "player_id": "<sleeper id>"}``.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

import httpx
from sqlalchemy.orm import Session

from app.models import Player, SyncLog, TrendingSignal

logger = logging.getLogger(__name__)

BASE_URL = "https://api.sleeper.app/v1"

FANTASY_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}

TRENDING_LOOKBACK_HOURS = 24
TRENDING_LIMIT = 100

#: Platform-ID columns Sleeper payloads can also carry, in match-priority
#: order after sleeper_id itself.
_FALLBACK_ID_COLUMNS = ("yahoo_id", "espn_id", "gsis_id")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@contextmanager
def _sync_log(db: Session, resource: str) -> Iterator[SyncLog]:
    log = SyncLog(resource=resource, started_at=_utcnow(), status="running")
    db.add(log)
    db.commit()
    try:
        yield log
    except Exception as exc:
        db.rollback()
        log.status = "error"
        log.finished_at = _utcnow()
        log.message = str(exc)[:2000]
        db.commit()
        raise
    else:
        if log.status == "running":
            log.status = "success"
        log.finished_at = _utcnow()
        db.commit()


def _player_name(payload: dict) -> str | None:
    full_name = payload.get("full_name")
    if full_name:
        return full_name
    first, last = payload.get("first_name"), payload.get("last_name")
    if first or last:
        return " ".join(part for part in (first, last) if part)
    return None


def _str_or_none(value) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _find_player(db: Session, sleeper_id: str, ids: dict[str, str | None]) -> Player | None:
    player = db.query(Player).filter(Player.sleeper_id == sleeper_id).first()
    if player is not None:
        return player
    for col in _FALLBACK_ID_COLUMNS:
        value = ids.get(col)
        if not value:
            continue
        player = db.query(Player).filter(getattr(Player, col) == value).first()
        if player is not None:
            return player
    return None


def _enrich_ids(player: Player, sleeper_id: str, ids: dict[str, str | None]) -> None:
    if player.sleeper_id is None:
        player.sleeper_id = sleeper_id
    elif player.sleeper_id != sleeper_id:
        logger.warning(
            "sleeper: sleeper_id mismatch for player %s (%s): existing=%r new=%r",
            player.id,
            player.full_name,
            player.sleeper_id,
            sleeper_id,
        )

    for col in _FALLBACK_ID_COLUMNS:
        value = ids.get(col)
        if not value:
            continue
        current = getattr(player, col)
        if current is None:
            setattr(player, col, value)
        elif current != value:
            logger.warning(
                "sleeper: %s mismatch for player %s (%s): existing=%r new=%r",
                col,
                player.id,
                player.full_name,
                current,
                value,
            )


def refresh_players(db: Session) -> str:
    """Sync player metadata (name/position/team/injury) from Sleeper."""
    with _sync_log(db, "sleeper_players") as log:
        response = httpx.get(f"{BASE_URL}/players/nfl", timeout=30)
        response.raise_for_status()
        payload = response.json()

        created = 0
        updated = 0
        for sleeper_id, entry in payload.items():
            position = entry.get("position")
            if position not in FANTASY_POSITIONS:
                continue

            name = _player_name(entry)
            if not name:
                continue
            # Sleeper's dump contains literal placeholder records.
            if name.lower() in ("player invalid", "duplicate player"):
                continue

            ids = {
                "yahoo_id": _str_or_none(entry.get("yahoo_id")),
                "espn_id": _str_or_none(entry.get("espn_id")),
                "gsis_id": _str_or_none(entry.get("gsis_id")),
            }

            player = _find_player(db, sleeper_id, ids)
            if player is None:
                player = Player(full_name=name, sleeper_id=sleeper_id)
                for col, value in ids.items():
                    if value:
                        setattr(player, col, value)
                db.add(player)
                created += 1
            else:
                _enrich_ids(player, sleeper_id, ids)
                updated += 1

            player.full_name = name
            player.position = position
            player.nfl_team = entry.get("team") or player.nfl_team
            player.injury_status = entry.get("injury_status") or None
            player.updated_at = _utcnow()
            db.flush()

        db.commit()
        log.message = f"{created} created, {updated} updated"
        return log.message


def _upsert_trending(
    db: Session, kind: str, entries: list[dict], fetched_at: datetime
) -> tuple[int, int]:
    matched_player_ids: set[int] = set()
    unmatched = 0

    for entry in entries:
        sleeper_id = _str_or_none(entry.get("player_id"))
        count = entry.get("count")
        if sleeper_id is None or count is None:
            continue

        player = db.query(Player).filter(Player.sleeper_id == sleeper_id).first()
        if player is None:
            unmatched += 1
            continue

        row = (
            db.query(TrendingSignal)
            .filter(
                TrendingSignal.player_id == player.id,
                TrendingSignal.source == "sleeper",
                TrendingSignal.kind == kind,
            )
            .one_or_none()
        )
        if row is None:
            row = TrendingSignal(player_id=player.id, source="sleeper", kind=kind)
            db.add(row)
        row.count = int(count)
        row.fetched_at = fetched_at
        matched_player_ids.add(player.id)

    stale_query = db.query(TrendingSignal).filter(
        TrendingSignal.source == "sleeper", TrendingSignal.kind == kind
    )
    if matched_player_ids:
        stale_query = stale_query.filter(
            TrendingSignal.player_id.notin_(matched_player_ids)
        )
    stale_query.delete(synchronize_session=False)

    db.commit()
    return len(matched_player_ids), unmatched


def refresh_trending(db: Session) -> str:
    """Sync add/drop trending counts from Sleeper."""
    with _sync_log(db, "sleeper_trending") as log:
        fetched_at = _utcnow()
        summary: list[str] = []

        for kind in ("add", "drop"):
            response = httpx.get(
                f"{BASE_URL}/players/nfl/trending/{kind}",
                params={
                    "lookback_hours": TRENDING_LOOKBACK_HOURS,
                    "limit": TRENDING_LIMIT,
                },
                timeout=30,
            )
            response.raise_for_status()
            entries = response.json()
            matched, unmatched = _upsert_trending(db, kind, entries, fetched_at)
            summary.append(f"{kind}: {matched} matched, {unmatched} unmatched")

        log.message = "; ".join(summary)
        return log.message
