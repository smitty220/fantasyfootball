"""NFL schedule ingestion + bye/opponent lookups.

Downloads the nflverse games/schedules dataset and stores the current
season's regular-season games as :class:`~app.models.NflGame` rows. Those rows
are what lets the rest of the app answer three questions no projection source
tells us directly: when is a player's team on bye, who do they play this week,
and how many games do they have left (which is what rest-of-season points
should actually be scaled by -- see ``evaluator._projection_points``).

Source
------
Confirmed live (2026-09-09) against
``https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv``
(HTTP 200, ~2.1MB, also mirrored at ``http://www.habitatring.com/games.csv``).
Columns used here: ``season``, ``game_type``, ``week``, ``gameday``
(``YYYY-MM-DD``), ``gametime`` (``HH:MM``, may be blank), ``home_team``,
``away_team``. The file carries every season from 1999 on; the 2026 season has
272 rows, all ``game_type == "REG"``, spread over weeks 1-18 -- so 32 teams x
17 games, i.e. exactly one bye apiece.

Team abbreviations
------------------
The CSV uses ``LA`` for the Rams (``LAC`` for the Chargers), and otherwise the
same 2-3 letter forms Sleeper uses (``GB``, ``KC``, ``JAX``, ``LV``, ``NE``,
``NO``, ``SF``, ``TB``, ``WAS``). Our ``Player.nfl_team`` is a mix: rows
created by Sleeper/Yahoo use that same convention (which is why it is the
canonical one here), but rows created by the nflverse *crosswalk* carry
MFL-style spellings (``GBP``, ``KCC``, ``NEP``, ``NOS``, ``SFO``, ``TBB``,
``LVR``, ``SDC``, ``RAM``, ``JAC``) -- a couple hundred players in the live DB.
:func:`normalize_team` folds both sides into the Sleeper form: it applies
``matching.normalize_team_abbr`` (WSH/JAC/OAK/SD/STL) and then the MFL-style
extras below.

Caching
-------
The CSV is large and changes rarely, so a copy is kept under ``data/cache/``
and reused for 24h -- the same pattern as ``app.services.crosswalk``.
"""

from __future__ import annotations

import csv
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import httpx
from sqlalchemy.orm import Session

from app.models import NflGame, Player, SyncLog
from app.services.matching import normalize_team_abbr
from app.services.yahoo.sync import current_nfl_season

logger = logging.getLogger(__name__)

SCHEDULE_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"
)

#: Where we cache the downloaded CSV, relative to the repo root.
CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "cache"
CACHE_FILE = CACHE_DIR / "nflverse_games.csv"
CACHE_TTL_SECONDS = 24 * 3600

#: The dataset's label for regular-season games (the rest are POST/PRE).
REGULAR_SEASON = "REG"

#: Regular-season weeks in the modern (17-game, 18-week) format.
REGULAR_SEASON_WEEKS = 18

#: Games each team plays in a full regular season -- the denominator the
#: rest-of-season scaling divides remaining games by.
GAMES_PER_SEASON = 17

#: Abbreviation variants ``matching.normalize_team_abbr`` doesn't cover, folded
#: into Sleeper's convention. ``LA``/``RAM`` come from the schedule CSV and the
#: crosswalk respectively; the rest are the MFL-style spellings the crosswalk
#: writes onto ``Player.nfl_team``.
_EXTRA_TEAM_ALIASES = {
    "LA": "LAR",
    "RAM": "LAR",
    "GBP": "GB",
    "KCC": "KC",
    "NEP": "NE",
    "NOS": "NO",
    "SFO": "SF",
    "TBB": "TB",
    "LVR": "LV",
    "SDC": "LAC",
    "ARZ": "ARI",
    "BLT": "BAL",
    "CLV": "CLE",
    "HST": "HOU",
}

#: Positions whose bye week is worth tracking. ``DST`` is the crosswalk/ESPN
#: spelling of Sleeper's ``DEF``, ``PK`` the crosswalk spelling of ``K``.
FANTASY_POSITIONS = frozenset({"QB", "RB", "WR", "TE", "K", "PK", "DEF", "DST"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_team(abbr: str | None) -> str:
    """Fold an NFL team abbreviation into Sleeper's convention.

    ``"LA"``/``"RAM"``/``"STL"`` -> ``"LAR"``, ``"GBP"`` -> ``"GB"``,
    ``"JAC"`` -> ``"JAX"``, and so on. Returns ``""`` for a missing value, and
    passes anything unrecognised through upper-cased (a genuinely unknown team
    then simply fails to join, which is the safe outcome everywhere it is used).
    """
    folded = normalize_team_abbr(abbr)
    return _EXTRA_TEAM_ALIASES.get(folded, folded)


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
    response = httpx.get(SCHEDULE_URL, timeout=60, follow_redirects=True)
    response.raise_for_status()
    return response.text


def _fetch_csv_text() -> str:
    """Return the schedule CSV text, using a same-day cache when present."""
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


def _parse_kickoff(gameday: str | None, gametime: str | None) -> datetime | None:
    """``"2026-09-09"`` + ``"20:20"`` -> a naive datetime; ``None`` if unusable.

    The time half is frequently blank for games the league hasn't slotted yet,
    in which case we keep the date at midnight rather than dropping the game.
    """
    day = _clean(gameday)
    if not day:
        return None
    try:
        parsed = datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        logger.warning("nfl_schedule: unparseable gameday %r", gameday)
        return None

    clock = _clean(gametime)
    if not clock:
        return parsed
    try:
        moment = datetime.strptime(clock, "%H:%M").time()
    except ValueError:
        return parsed
    return parsed.replace(hour=moment.hour, minute=moment.minute)


def _season_games(csv_text: str, season: int) -> list[dict]:
    """The ``season``'s regular-season games, with teams normalized."""
    games: list[dict] = []
    for raw in csv.DictReader(csv_text.splitlines()):
        if _clean(raw.get("game_type")) != REGULAR_SEASON:
            continue
        try:
            row_season = int(_clean(raw.get("season")) or "")
            week = int(_clean(raw.get("week")) or "")
        except ValueError:
            continue
        if row_season != season:
            continue

        home = normalize_team(raw.get("home_team"))
        away = normalize_team(raw.get("away_team"))
        if not home or not away:
            continue

        games.append(
            {
                "season": row_season,
                "week": week,
                "home_team": home,
                "away_team": away,
                "kickoff": _parse_kickoff(raw.get("gameday"), raw.get("gametime")),
            }
        )
    return games


def _upsert_games(db: Session, games: list[dict]) -> tuple[int, int]:
    """Insert/update ``games`` keyed on (season, week, home_team)."""
    if not games:
        return 0, 0

    seasons = {game["season"] for game in games}
    existing = {
        (row.season, row.week, row.home_team): row
        for row in db.query(NflGame).filter(NflGame.season.in_(sorted(seasons))).all()
    }

    created = 0
    updated = 0
    for game in games:
        key = (game["season"], game["week"], game["home_team"])
        row = existing.get(key)
        if row is None:
            row = NflGame(**game)
            db.add(row)
            existing[key] = row
            created += 1
            continue
        if row.away_team != game["away_team"] or row.kickoff != game["kickoff"]:
            row.away_team = game["away_team"]
            row.kickoff = game["kickoff"]
            updated += 1
    db.commit()
    return created, updated


def bye_weeks(db: Session, season: int) -> dict[str, int]:
    """``team -> bye week`` for ``season``, derived from the games on file.

    A team's bye is the week in ``1..18`` it has no game in. A team with more
    than one missing week (an incomplete download, or a future format change)
    gets its *first* missing week and a warning; a team missing none at all is
    left out entirely rather than guessed at.
    """
    played: dict[str, set[int]] = {}
    for home, away, week in db.query(
        NflGame.home_team, NflGame.away_team, NflGame.week
    ).filter(NflGame.season == season):
        played.setdefault(home, set()).add(week)
        played.setdefault(away, set()).add(week)

    byes: dict[str, int] = {}
    for team, weeks in played.items():
        missing = [w for w in range(1, REGULAR_SEASON_WEEKS + 1) if w not in weeks]
        if not missing:
            logger.warning(
                "nfl_schedule: %s has a game in every week of %s; no bye recorded",
                team,
                season,
            )
            continue
        if len(missing) > 1:
            logger.warning(
                "nfl_schedule: %s is missing %d weeks in %s (%s); using the first "
                "as its bye",
                team,
                len(missing),
                season,
                ", ".join(str(w) for w in missing),
            )
        byes[team] = missing[0]
    return byes


def refresh_bye_weeks(db: Session, season: int) -> int:
    """Recompute ``Player.bye_week`` for every fantasy-position player.

    Full recompute, not a merge: a player whose team has no bye on file (or no
    recognisable team at all) has ``bye_week`` cleared, so a trade or a stale
    row can never leave last week's answer sitting there. Returns how many
    player rows actually changed.
    """
    byes = bye_weeks(db, season)
    if not byes:
        return 0

    changed = 0
    for player in db.query(Player).filter(Player.position.isnot(None)):
        if (player.position or "").strip().upper() not in FANTASY_POSITIONS:
            continue
        bye = byes.get(normalize_team(player.nfl_team))
        if player.bye_week != bye:
            player.bye_week = bye
            changed += 1
    db.commit()
    return changed


def refresh_schedule(db: Session) -> str:
    """Load the current season's NFL schedule and recompute bye weeks."""
    with _sync_log(db, "nfl_schedule") as log:
        season = current_nfl_season()
        games = _season_games(_fetch_csv_text(), season)
        created, updated = _upsert_games(db, games)
        byes = refresh_bye_weeks(db, season)

        log.message = (
            f"{season}: {len(games)} games ({created} created, {updated} updated), "
            f"{byes} bye weeks set"
        )
        return log.message


# --- lookups used by the evaluator -----------------------------------------


def team_remaining_games(db: Session, season: int, from_week: int = 1) -> dict[str, int]:
    """``team -> games in week >= from_week``, home and away together.

    Only teams with at least one game left appear, so an empty mapping means
    "we know nothing about this season's schedule" *or* "the season is over" --
    callers treat both the same way (no scaling), which is the conservative
    reading in either case.
    """
    counts: dict[str, int] = {}
    for home, away in db.query(NflGame.home_team, NflGame.away_team).filter(
        NflGame.season == season, NflGame.week >= from_week
    ):
        counts[home] = counts.get(home, 0) + 1
        counts[away] = counts.get(away, 0) + 1
    return counts


def team_opponent(db: Session, season: int, week: int | None) -> dict[str, str]:
    """``team -> "vs OPP"`` (home) or ``"@ OPP"`` (away) for one week.

    A team absent from the mapping has no game that week: on a bye if the
    mapping is non-empty, unknown if it is empty (no schedule loaded, or
    ``week`` is ``None``).
    """
    if week is None:
        return {}

    opponents: dict[str, str] = {}
    for home, away in db.query(NflGame.home_team, NflGame.away_team).filter(
        NflGame.season == season, NflGame.week == week
    ):
        opponents[home] = f"vs {away}"
        opponents[away] = f"@ {home}"
    return opponents
