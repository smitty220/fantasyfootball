"""Actual weekly player stats from nflverse -- the yardstick for projections.

Every other ingester in this package writes ``Projection`` rows: what a source
*thinks* will happen. This one writes :class:`~app.models.ActualStat` rows:
what did happen. Those rows are what
``app.routers.accuracy`` grades each projection source against, and they are
kept in a separate table on purpose so the evaluator -- which blends every
``Projection`` row it can find for a player-week -- can never accidentally
score a lineup off hindsight.

Source
------
VERIFIED live on 2026-09-09 against the current nflverse release:

``https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv``

* The GitHub releases API lists a ``stats_player`` release carrying
  ``stats_player_week_<year>.csv`` for every season 1999-2025 (plus ``_reg``,
  ``_post`` and ``_regpost`` season aggregates, which we don't want -- we need
  one row per player *per week*).
* The older ``player_stats`` release (``player_stats_{season}.csv``,
  ``player_stats_kicking_{season}.csv``, ``player_stats_def_{season}.csv``) is
  frozen: its newest season asset is **2024**. It is nflverse's superseded
  layout and is deliberately not used here.
* ``stats_player_week_2026.csv`` returns **404 today** -- the 2026 season has
  not kicked off yet, so nflverse has not cut the file. :func:`refresh_actuals`
  treats that as "nothing to ingest yet" (a successful, empty sync) rather than
  an error, and will start finding rows the week after week 1 is played.
* The 2025 file is ~8.7MB / 19,422 rows / 150 columns, covering
  ``season_type`` ``REG`` (weeks 1-18) and ``POST`` (19-22). Only ``REG`` rows
  are ingested: fantasy weeks are regular-season weeks, and a week-19+ actual
  would silently pair with nothing.

Column mapping (VERIFIED against the real 2025 header row unless flagged)
------------------------------------------------------------------------
Passing: ``completions``->``pass_cmp``, ``attempts``->``pass_att``,
``passing_yards``->``pass_yds``, ``passing_tds``->``pass_td``,
``passing_interceptions``->``pass_int`` (NOT ``interceptions`` -- that name
does not exist in this dataset; the defensive column is ``def_interceptions``),
``sacks_suffered``->``pass_sacked`` (NOT ``sacks``; ``def_sacks`` is the
defender's side of the same play), ``passing_2pt_conversions``->``pass_2pt``.

Rushing: ``carries``->``rush_att``, ``rushing_yards``->``rush_yds``,
``rushing_tds``->``rush_td``, ``rushing_2pt_conversions``->``rush_2pt``.

Receiving: ``receptions``->``rec``, ``receiving_yards``->``rec_yds``,
``receiving_tds``->``rec_td``, ``receiving_2pt_conversions``->``rec_2pt``.

Misc offense: ``fumbles_lost_total``->``fum_lost``. VERIFIED that this is the
right column rather than the ``sack_fumbles_lost + rushing_fumbles_lost +
receiving_fumbles_lost`` sum the task suggested: on the 2025 file 253 rows
carry a lost fumble and 39 of them (return men -- Marvin Mims, Xavier Gipson,
...) have a nonzero ``fumbles_lost_total`` with all three component columns at
zero, i.e. the components miss fumbles lost on returns. Fantasy scoring counts
those, so the combined column wins.
``special_teams_tds``->``ret_td``;
``punt_return_yards + kickoff_return_yards``->``ret_yds``.

Kicking (K is ingested from this same file -- there is no separate kicking
dataset in the current release, the columns are simply present on every row):
``fg_made_0_19``/``fg_made_20_29``/``fg_made_30_39``/``fg_made_40_49``
-> the matching ``fg_0_19``.. ``fg_40_49`` buckets, and
``fg_made_50_59 + fg_made_60_`` -> ``fg_50_plus`` (note the real column name
ends in a bare underscore: ``fg_made_60_``). ``pat_made``->``xp_made``.
The plain ``fg_made`` total is deliberately **dropped**: our scoring rules
award points for both ``fg_made`` and the distance buckets (see
``scoring._STANDARD``), so storing both would double-count every kick.

ASSUMED (small, flagged): ``fg_miss`` is ``fg_missed + fg_blocked`` and
``xp_miss`` is ``pat_missed + pat_blocked``. VERIFIED that these are disjoint
columns (``fg_att == fg_made + fg_missed + fg_blocked`` holds on every 2025
row, likewise for PATs), so a blocked kick is otherwise uncounted; treating it
as a miss matches how Yahoo charges an unsuccessful attempt. Both keys score
0 in every current preset, so the choice is low-stakes either way.

Not mapped: ``fantasy_points``/``fantasy_points_ppr`` (nflverse's own scoring,
useless to us -- we score under each league's rules), the advanced/efficiency
columns (EPA, air yards, WOPR, ...), and the per-player defensive columns.

Team defense (DST) is **not** ingested. This dataset is per-player: its
defensive rows are individual defenders, and the two figures a DST line lives
or dies by -- points allowed and yards allowed -- are not in it at any
granularity. That needs the separate ``stats_team`` release plus a
team->``Player`` (position ``DEF``) join, which is a different job from this
one. So grading covers offense + kickers; DST projections simply never grade.

Caching
-------
Same pattern as ``app.services.nfl_schedule``/``crosswalk``: the CSV is large
and only changes when games are played, so a per-season copy lives under
``data/cache/`` and is reused for 24h.
"""

from __future__ import annotations

import csv
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import httpx
from sqlalchemy.orm import Session

from app.models import ActualStat, Player, SyncLog
from app.services.matching import normalize_name, normalize_position
from app.services.stats import clean_stat_line
from app.services.yahoo.sync import current_nfl_season

logger = logging.getLogger(__name__)

#: Registry/SyncLog resource name (must match the key in
#: ``app.routers.data.REFRESH_REGISTRY`` -- that endpoint reads back the latest
#: SyncLog row by resource name).
RESOURCE = "nfl_actuals"

STATS_URL_TEMPLATE = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "stats_player/stats_player_week_{season}.csv"
)

#: Where we cache the downloaded CSV, relative to the repo root.
CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "cache"
CACHE_TTL_SECONDS = 24 * 3600

#: The dataset's label for regular-season rows (the other value is ``POST``).
REGULAR_SEASON = "REG"

#: Dataset ``position`` values worth storing. Everything else in the file is a
#: defender, lineman, punter or long-snapper -- none of which our ``players``
#: table even carries, so keeping them would only inflate the unmatched count.
#: ``FB`` is in the file and is an ``RB`` on our side.
OFFENSE_POSITIONS = frozenset({"QB", "RB", "FB", "WR", "TE", "K"})


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


def _cache_file(season: int) -> Path:
    return CACHE_DIR / f"nflverse_stats_player_week_{season}.csv"


def _download_csv(season: int) -> str | None:
    """The season's weekly stats CSV, or ``None`` if nflverse has none yet.

    A 404 is the normal state between the schedule being published and the
    first games being played, so it is not an error -- see the module
    docstring.
    """
    response = httpx.get(
        STATS_URL_TEMPLATE.format(season=season), timeout=120, follow_redirects=True
    )
    if response.status_code == 404:
        logger.info("actuals: nflverse has no weekly stats for %s yet", season)
        return None
    response.raise_for_status()
    return response.text


def _fetch_csv_text(season: int) -> str | None:
    """Return the season's stats CSV text, using a same-day cache when present."""
    cache_file = _cache_file(season)
    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < CACHE_TTL_SECONDS:
            return cache_file.read_text()

    text = _download_csv(season)
    if text is None:
        return None
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(text)
    return text


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value if value and value != "NA" else None


def _num(row: dict[str, Any], *columns: str) -> float | None:
    """Sum of ``columns`` on ``row``; ``None`` when none of them has a value.

    ``None`` (rather than 0) keeps ``clean_stat_line`` from having to tell a
    real zero from a missing column, and keeps a column disappearing upstream
    from quietly reading as "the player did nothing".
    """
    total = 0.0
    seen = False
    for column in columns:
        raw = _clean(row.get(column))
        if raw is None:
            continue
        try:
            total += float(raw)
        except ValueError:
            logger.warning("actuals: non-numeric %s=%r", column, raw)
            continue
        seen = True
    return total if seen else None


def translate_stat_line(row: dict[str, Any]) -> dict[str, float]:
    """One nflverse weekly row -> a canonical stat line.

    See the module docstring for the column-by-column provenance. Returns
    ``{}`` for a row with nothing fantasy-relevant on it (``clean_stat_line``
    drops zeros), which callers treat as "no line to store".
    """
    translated: dict[str, float | None] = {
        # passing
        "pass_cmp": _num(row, "completions"),
        "pass_att": _num(row, "attempts"),
        "pass_yds": _num(row, "passing_yards"),
        "pass_td": _num(row, "passing_tds"),
        "pass_int": _num(row, "passing_interceptions"),
        "pass_sacked": _num(row, "sacks_suffered"),
        "pass_2pt": _num(row, "passing_2pt_conversions"),
        # rushing
        "rush_att": _num(row, "carries"),
        "rush_yds": _num(row, "rushing_yards"),
        "rush_td": _num(row, "rushing_tds"),
        "rush_2pt": _num(row, "rushing_2pt_conversions"),
        # receiving
        "rec": _num(row, "receptions"),
        "rec_yds": _num(row, "receiving_yards"),
        "rec_td": _num(row, "receiving_tds"),
        "rec_2pt": _num(row, "receiving_2pt_conversions"),
        # misc offense
        "fum_lost": _num(row, "fumbles_lost_total"),
        "ret_td": _num(row, "special_teams_tds"),
        "ret_yds": _num(row, "punt_return_yards", "kickoff_return_yards"),
        # kicking (no plain fg_made -- see module docstring)
        "fg_0_19": _num(row, "fg_made_0_19"),
        "fg_20_29": _num(row, "fg_made_20_29"),
        "fg_30_39": _num(row, "fg_made_30_39"),
        "fg_40_49": _num(row, "fg_made_40_49"),
        "fg_50_plus": _num(row, "fg_made_50_59", "fg_made_60_"),
        "fg_miss": _num(row, "fg_missed", "fg_blocked"),  # ASSUMED, see docstring
        "xp_made": _num(row, "pat_made"),
        "xp_miss": _num(row, "pat_missed", "pat_blocked"),  # ASSUMED
    }
    return clean_stat_line(translated)


# --- player matching -------------------------------------------------------


class _PlayerIndex:
    """In-memory ``gsis_id`` / name+position lookups over the players table.

    The per-row alternative (``matching.find_by_name_position``) re-reads every
    ``Player`` row on each call, which is fine for a few hundred projections
    but not for the ~7,000 offensive player-weeks in a full season file. Same
    normalization (``matching.normalize_name``/``normalize_position``) and the
    same ambiguity policy: a name+position that two players share resolves to
    nothing rather than to a coin flip.
    """

    def __init__(self, db: Session) -> None:
        self._by_gsis: dict[str, Player] = {}
        self._by_name: dict[tuple[str, str], list[Player]] = {}
        for player in db.query(Player).all():
            gsis = (player.gsis_id or "").strip()
            if gsis:
                self._by_gsis.setdefault(gsis, player)
            key = (normalize_name(player.full_name), normalize_position(player.position))
            if key[0]:
                self._by_name.setdefault(key, []).append(player)

    def find(
        self, gsis_id: str | None, full_name: str | None, position: str | None
    ) -> tuple[Player | None, bool]:
        """Resolve a dataset row to a player; returns ``(player, matched_by_id)``.

        ``gsis_id`` first (the crosswalk fills it in for most of the league),
        then normalized name+position. A name-matched player missing a
        ``gsis_id`` gets it backfilled, so the next refresh matches by ID --
        the same enrichment ``fantasypros._match_player`` does with its own ID.
        """
        if gsis_id:
            player = self._by_gsis.get(gsis_id)
            if player is not None:
                return player, True

        key = (normalize_name(full_name), normalize_position(position))
        if not key[0]:
            return None, False
        candidates = self._by_name.get(key) or []
        if len(candidates) != 1:
            return None, False

        player = candidates[0]
        if gsis_id and not player.gsis_id:
            player.gsis_id = gsis_id
            self._by_gsis.setdefault(gsis_id, player)
        return player, False


# --- ingestion -------------------------------------------------------------


def _parse_rows(csv_text: str, season: int) -> list[dict[str, Any]]:
    """Regular-season offensive/kicker rows for ``season``, already translated.

    Each entry is ``{"gsis_id", "name", "position", "week", "stat_json"}``.
    Rows with no fantasy-relevant production at all are dropped: an actual of
    "nothing" and no actual on file grade identically (both leave the
    player-week ungraded), and keeping them would write thousands of empty rows.
    """
    parsed: list[dict[str, Any]] = []
    for raw in csv.DictReader(csv_text.splitlines()):
        if _clean(raw.get("season_type")) != REGULAR_SEASON:
            continue
        try:
            row_season = int(_clean(raw.get("season")) or "")
            week = int(_clean(raw.get("week")) or "")
        except ValueError:
            continue
        if row_season != season:
            continue

        position = (_clean(raw.get("position")) or "").upper()
        if position not in OFFENSE_POSITIONS:
            continue

        stat_json = translate_stat_line(raw)
        if not stat_json:
            continue

        parsed.append(
            {
                "gsis_id": _clean(raw.get("player_id")),
                "name": _clean(raw.get("player_display_name"))
                or _clean(raw.get("player_name")),
                "position": position,
                "week": week,
                "stat_json": stat_json,
            }
        )
    return parsed


def _upsert_actuals(
    db: Session, season: int, rows: list[tuple[int, int, dict]], fetched_at: datetime
) -> tuple[int, int]:
    """Insert/update ``(player_id, week, stat_json)`` triples; returns (created, updated).

    Idempotent by construction: the season's existing rows are read once into a
    ``(player_id, week)`` map, so re-running over the same file rewrites the
    same rows rather than duplicating them.
    """
    if not rows:
        return 0, 0

    existing = {
        (row.player_id, row.week): row
        for row in db.query(ActualStat).filter(ActualStat.season == season).all()
    }

    created = 0
    updated = 0
    for player_id, week, stat_json in rows:
        row = existing.get((player_id, week))
        if row is None:
            row = ActualStat(
                player_id=player_id,
                season=season,
                week=week,
                stat_json=stat_json,
                fetched_at=fetched_at,
            )
            db.add(row)
            existing[(player_id, week)] = row
            created += 1
            continue
        if row.stat_json != stat_json:
            row.stat_json = stat_json
            updated += 1
        row.fetched_at = fetched_at
    db.commit()
    return created, updated


def refresh_actuals(db: Session, season: int | None = None) -> dict:
    """Ingest every completed week of ``season``'s actual player stats.

    The whole season file is processed on every run, not just the latest week:
    nflverse revises earlier weeks (stat corrections land days later), and the
    upsert makes a full pass cost nothing but a little CPU.

    Returns a summary dict; also written to the ``nfl_actuals`` SyncLog row.
    """
    with _sync_log(db, RESOURCE) as log:
        season = season if season is not None else current_nfl_season()
        csv_text = _fetch_csv_text(season)
        if csv_text is None:
            log.message = (
                f"{season}: nflverse has no weekly player stats published yet; "
                "nothing ingested"
            )
            return {
                "season": season,
                "available": False,
                "weeks": [],
                "saved": 0,
                "created": 0,
                "updated": 0,
                "matched_by_gsis": 0,
                "matched_by_name": 0,
                "unmatched": 0,
            }

        parsed = _parse_rows(csv_text, season)
        index = _PlayerIndex(db)

        resolved: list[tuple[int, int, dict]] = []
        matched_by_gsis = 0
        matched_by_name = 0
        unmatched = 0
        unmatched_names: set[str] = set()
        for entry in parsed:
            player, by_id = index.find(
                entry["gsis_id"], entry["name"], entry["position"]
            )
            if player is None:
                unmatched += 1
                if entry["name"]:
                    unmatched_names.add(entry["name"])
                continue
            if by_id:
                matched_by_gsis += 1
            else:
                matched_by_name += 1
            resolved.append((player.id, entry["week"], entry["stat_json"]))

        fetched_at = _utcnow()
        created, updated = _upsert_actuals(db, season, resolved, fetched_at)

        weeks = sorted({week for _, week, _ in resolved})
        log.message = (
            f"{season}: {len(resolved)} player-week actual(s) over weeks "
            f"{weeks or '-'} ({created} created, {updated} updated; "
            f"{matched_by_gsis} matched by gsis_id, {matched_by_name} by name, "
            f"{unmatched} unmatched across {len(unmatched_names)} player(s))"
        )
        return {
            "season": season,
            "available": True,
            "weeks": weeks,
            "saved": len(resolved),
            "created": created,
            "updated": updated,
            "matched_by_gsis": matched_by_gsis,
            "matched_by_name": matched_by_name,
            "unmatched": unmatched,
        }


def actual_weeks(db: Session, season: int) -> list[int]:
    """Weeks of ``season`` with at least one actual stat line on file."""
    rows = (
        db.query(ActualStat.week)
        .filter(ActualStat.season == season)
        .distinct()
        .all()
    )
    return sorted(week for (week,) in rows)
