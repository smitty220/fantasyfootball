"""FantasyCalc trade values (https://api.fantasycalc.com).

Response shape confirmed live on 2026-09-06 against
``GET /values/current?isDynasty={true,false}&numQbs=1&numTeams=12&ppr=1``:
a bare JSON list of objects shaped like::

    {
        "player": {"id": 9821, "name": "Jahmyr Gibbs", "sleeperId": "9221",
                    "position": "RB", "maybeTeam": "DET", ...},
        "value": 10161,
        "trend30Day": 80,
        ...
    }

In dynasty mode (``isDynasty=true``) the list also includes synthetic
"players" representing future draft picks (``player.position == "PICK"``,
e.g. name "2027 1st (Early)"); these aren't real players and are skipped
entirely rather than counted as unmatched. Redraft mode has no such rows.
Every real player entry we saw carried a ``sleeperId``, but the fallback
name+position match is kept per spec for the entries that don't.
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

import httpx
from sqlalchemy.orm import Session

from app.models import Player, SyncLog, TradeValue

logger = logging.getLogger(__name__)

VALUES_URL = "https://api.fantasycalc.com/values/current"

SOURCE = "fantasycalc"

#: (isDynasty, format label) pairs to fetch.
FORMATS = ((False, "redraft"), (True, "dynasty"))

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
_PUNCTUATION_RE = re.compile(r"[.'’]")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalize_name(name: str) -> str:
    cleaned = _PUNCTUATION_RE.sub("", name.lower())
    tokens = [t for t in cleaned.split() if t not in _SUFFIXES]
    return " ".join(tokens)


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


def _fetch_values(is_dynasty: bool) -> list[dict]:
    response = httpx.get(
        VALUES_URL,
        params={
            "isDynasty": "true" if is_dynasty else "false",
            "numQbs": 1,
            "numTeams": 12,
            "ppr": 1,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _match_player(db: Session, player_info: dict) -> Player | None:
    sleeper_id = player_info.get("sleeperId")
    if sleeper_id:
        player = db.query(Player).filter(Player.sleeper_id == str(sleeper_id)).first()
        if player is not None:
            return player

    name = player_info.get("name")
    position = player_info.get("position")
    if not name or not position:
        return None

    target = _normalize_name(name)
    candidates = db.query(Player).filter(Player.position == position).all()
    matches = [p for p in candidates if _normalize_name(p.full_name) == target]
    if len(matches) == 1:
        return matches[0]
    return None


def _upsert_trade_value(
    db: Session, player: Player, format_label: str, entry: dict, fetched_at: datetime
) -> None:
    row = (
        db.query(TradeValue)
        .filter(
            TradeValue.player_id == player.id,
            TradeValue.source == SOURCE,
            TradeValue.format == format_label,
        )
        .one_or_none()
    )
    if row is None:
        row = TradeValue(player_id=player.id, source=SOURCE, format=format_label)
        db.add(row)
    row.value = entry.get("value")
    row.trend_30d = entry.get("trend30Day")
    row.fetched_at = fetched_at


def refresh_trade_values(db: Session) -> str:
    """Pull redraft + dynasty trade values from FantasyCalc."""
    with _sync_log(db, "fantasycalc") as log:
        fetched_at = _utcnow()
        summary: list[str] = []

        for is_dynasty, format_label in FORMATS:
            entries = _fetch_values(is_dynasty)
            matched = 0
            unmatched = 0

            for entry in entries:
                player_info = entry.get("player") or {}
                if player_info.get("position") == "PICK":
                    continue  # a future draft pick, not a real player

                player = _match_player(db, player_info)
                if player is None:
                    unmatched += 1
                    continue

                _upsert_trade_value(db, player, format_label, entry, fetched_at)
                matched += 1

            db.commit()
            summary.append(f"{format_label}: {matched} matched, {unmatched} unmatched")

        log.message = "; ".join(summary)
        return log.message
