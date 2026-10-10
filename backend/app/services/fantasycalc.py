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
from typing import Iterator, NamedTuple

import httpx
from sqlalchemy.orm import Session

from app.models import League, Player, SyncLog, TradeValue

logger = logging.getLogger(__name__)

VALUES_URL = "https://api.fantasycalc.com/values/current"

SOURCE = "fantasycalc"

#: Which FantasyCalc value sets to fetch: the ``isDynasty`` flag and its label.
MODES = ((False, "redraft"), (True, "dynasty"))

_PPR_STEPS = (0.0, 0.5, 1.0)


class Profile(NamedTuple):
    """The league traits FantasyCalc prices a player against."""

    num_teams: int = 12
    num_qbs: int = 1
    ppr: float = 1.0

    def label(self, dynasty: bool) -> str:
        """``TradeValue.format`` string, e.g. ``redraft:10:2:1`` or
        ``dynasty:12:1:0.5`` (teams:QBs:PPR; at most 16 chars, the column width)."""
        mode = "dynasty" if dynasty else "redraft"
        return f"{mode}:{self.num_teams}:{self.num_qbs}:{self.ppr:g}"


DEFAULT_PROFILE = Profile()


def league_profile(league: League | None) -> Profile:
    """Derive a league's FantasyCalc profile from its stored settings."""
    if league is None:
        return DEFAULT_PROFILE
    from app.services import evaluator, scoring  # lazy: evaluator imports this module

    num_teams = league.num_teams or DEFAULT_PROFILE.num_teams
    slots = evaluator.league_roster_slots(league) if league.settings_json else {}
    qb_seats = 0
    superflex = False
    for raw, count in slots.items():
        label = evaluator.normalize_slot(raw)
        if label == "SUPERFLEX" and count:
            superflex = True
        elif label == "QB":
            qb_seats += count
    num_qbs = 2 if superflex or qb_seats > 1 else 1

    rules = scoring.league_rules(league.settings_json)
    rec = float((rules.get("per_stat") or {}).get("rec", 0.5))
    ppr = min(_PPR_STEPS, key=lambda step: abs(step - rec))
    return Profile(int(num_teams), num_qbs, ppr)


def trade_format(league: League | None, dynasty: bool = False) -> str:
    """The ``TradeValue.format`` a league's consumers should read."""
    return league_profile(league).label(dynasty)


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


def _fetch_values(is_dynasty: bool, profile: Profile = DEFAULT_PROFILE) -> list[dict]:
    response = httpx.get(
        VALUES_URL,
        params={
            "isDynasty": "true" if is_dynasty else "false",
            "numQbs": profile.num_qbs,
            "numTeams": profile.num_teams,
            "ppr": profile.ppr,
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
    """Pull redraft + dynasty values for every distinct league profile.

    Values for profiles no league uses any more are deleted.
    """
    with _sync_log(db, "fantasycalc") as log:
        fetched_at = _utcnow()
        summary: list[str] = []

        profiles = sorted(
            {league_profile(league) for league in db.query(League).all()}
        ) or [DEFAULT_PROFILE]
        keep_labels: set[str] = set()

        for profile in profiles:
            for is_dynasty, _mode in MODES:
                format_label = profile.label(is_dynasty)
                keep_labels.add(format_label)
                entries = _fetch_values(is_dynasty, profile)
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
                summary.append(
                    f"{format_label}: {matched} matched, {unmatched} unmatched"
                )

        removed = (
            db.query(TradeValue)
            .filter(
                TradeValue.source == SOURCE,
                TradeValue.format.notin_(sorted(keep_labels)),
            )
            .delete(synchronize_session=False)
        )
        db.commit()
        if removed:
            summary.append(f"removed {removed} stale rows")

        log.message = "; ".join(summary)
        return log.message
