"""nflverse (DynastyProcess) player-ID crosswalk.

Downloads the ``ff_playerids`` CSV maintained at
https://github.com/dynastyprocess/data (``files/db_playerids.csv``) and uses
it to fill in the per-platform IDs on :class:`~app.models.Player` rows, and to
create rows for players we haven't seen from any other source yet.

The CSV is quite large (~2.5MB) and only needs refreshing occasionally, so a
copy is cached under ``data/cache/`` and reused for 24h.

Confirmed (2026-09-06) against the live file: columns include ``mfl_id``,
``sportradar_id``, ``fantasypros_id``, ``gsis_id``, ``pff_id``, ``sleeper_id``,
``nfl_id``, ``espn_id``, ``yahoo_id``, ..., ``name``, ``merge_name``,
``position``, ``team``, ... Missing values are the literal string ``"NA"``.
The dataset only carries individual players (no team defenses), and uses
``PK`` for kickers rather than ``K``.
"""

from __future__ import annotations

import csv
import logging
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import httpx
from sqlalchemy.orm import Session

from app.models import Player, SyncLog

logger = logging.getLogger(__name__)

CROSSWALK_URL = (
    "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv"
)

#: Where we cache the downloaded CSV, relative to the repo root.
CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "cache"
CACHE_FILE = CACHE_DIR / "db_playerids.csv"
CACHE_TTL_SECONDS = 24 * 3600

#: Position values (as they appear in the CSV) we care about, mapped to our
#: canonical position strings. The CSV has no team-defense rows at all.
POSITION_MAP = {
    "QB": "QB",
    "RB": "RB",
    "WR": "WR",
    "TE": "TE",
    "PK": "K",
}

#: Platform-ID columns we resolve/enrich, in match-priority order.
ID_COLUMNS = ("yahoo_id", "sleeper_id", "espn_id", "fantasypros_id", "gsis_id")

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
_PUNCTUATION_RE = re.compile(r"[.'’]")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_name(name: str) -> str:
    """Lowercase, drop punctuation, and strip Jr/Sr/II/III/IV suffixes.

    Mirrors the CSV's own ``merge_name`` convention closely enough that
    normalizing either side yields the same key (verified against the live
    file: e.g. "Amon-Ra St. Brown" -> "amon-ra st brown", "D'Andre Swift" ->
    "dandre swift", "Michael Penix Jr." -> "michael penix").
    """
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


def _download_csv() -> str:
    response = httpx.get(CROSSWALK_URL, timeout=30, follow_redirects=True)
    response.raise_for_status()
    return response.text


def _fetch_csv_text() -> str:
    """Return the crosswalk CSV text, using a same-day cache when present."""
    if CACHE_FILE.exists():
        age = time.time() - CACHE_FILE.stat().st_mtime
        if age < CACHE_TTL_SECONDS:
            return CACHE_FILE.read_text()

    text = _download_csv()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(text)
    return text


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value if value and value != "NA" else None


def _relevant_rows(csv_text: str) -> list[dict]:
    reader = csv.DictReader(csv_text.splitlines())
    rows = []
    for raw in reader:
        position = POSITION_MAP.get(_clean(raw.get("position")) or "")
        if position is None:
            continue
        row = {
            "name": _clean(raw.get("name")),
            "merge_name": _clean(raw.get("merge_name")),
            "position": position,
            "team": _clean(raw.get("team")),
        }
        for col in ID_COLUMNS:
            row[col] = _clean(raw.get(col))
        if row["name"] is None:
            continue
        rows.append(row)
    return rows


def _find_existing_player(db: Session, row: dict) -> Player | None:
    for col in ID_COLUMNS:
        value = row.get(col)
        if not value:
            continue
        player = db.query(Player).filter(getattr(Player, col) == value).first()
        if player is not None:
            return player
    return None


def _enrich_ids(player: Player, row: dict) -> bool:
    """Fill NULL ID columns from ``row``; never overwrite a differing value.

    Returns True if anything changed.
    """
    changed = False
    for col in ID_COLUMNS:
        value = row.get(col)
        if not value:
            continue
        current = getattr(player, col)
        if current is None:
            setattr(player, col, value)
            changed = True
        elif current != value:
            logger.warning(
                "crosswalk: %s mismatch for player %s (%s): existing=%r csv=%r",
                col,
                player.id,
                player.full_name,
                current,
                value,
            )
    return changed


def refresh_crosswalk(db: Session) -> str:
    """Refresh Player rows from the nflverse ff_playerids crosswalk."""
    with _sync_log(db, "crosswalk") as log:
        csv_text = _fetch_csv_text()
        rows = _relevant_rows(csv_text)

        created = 0
        enriched = 0
        for row in rows:
            player = _find_existing_player(db, row)
            if player is None:
                player = Player(
                    full_name=row["name"],
                    position=row["position"],
                    nfl_team=row["team"],
                    updated_at=_utcnow(),
                )
                for col in ID_COLUMNS:
                    if row.get(col):
                        setattr(player, col, row[col])
                db.add(player)
                created += 1
            else:
                if _enrich_ids(player, row):
                    enriched += 1
            db.flush()
        db.commit()

        # Second pass: normalized-name matching for players still missing
        # every platform ID (e.g. rookies not yet in any other source).
        missing_players = (
            db.query(Player)
            .filter(
                Player.yahoo_id.is_(None),
                Player.sleeper_id.is_(None),
                Player.espn_id.is_(None),
                Player.fantasypros_id.is_(None),
                Player.gsis_id.is_(None),
            )
            .all()
        )

        by_key: dict[tuple[str, str], list[dict]] = {}
        for row in rows:
            key = (row["merge_name"] or normalize_name(row["name"]), row["position"])
            by_key.setdefault(key, []).append(row)

        name_matched = 0
        ambiguous = 0
        for player in missing_players:
            if not player.position:
                continue
            key = (normalize_name(player.full_name), player.position)
            candidates = by_key.get(key, [])
            if len(candidates) == 1:
                if _enrich_ids(player, candidates[0]):
                    name_matched += 1
            elif len(candidates) > 1:
                ambiguous += 1
        db.commit()

        unmatched = len(missing_players) - name_matched
        log.message = (
            f"{created} created, {enriched} enriched, {name_matched} name-matched, "
            f"{ambiguous} ambiguous, {unmatched} still unmatched"
        )
        return log.message
