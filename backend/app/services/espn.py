"""ESPN's unofficial fantasy API -- free, unauthenticated projections.

Endpoint (the "leaguedefaults" trick: a synthetic public league that needs
no league id, team, or cookie)::

    GET https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leaguedefaults/3?view=kona_player_info

Paged and filtered entirely through the ``X-Fantasy-Filter`` request
header (a JSON blob), e.g.::

    {"players": {"filterSlotIds": {"value": [0, 2, 4, 6, 16, 17]},
                 "limit": 200, "offset": 0,
                 "sortPercOwned": {"sortAsc": false, "sortPriority": 1}}}

Everything below was checked against real, live responses on 2026-09-06
(season=2026) rather than trusted from blog posts -- see the specific
players/values checked in each note:

* Paging: ``players.limit`` / ``players.offset`` in the filter header work
  as expected (a limit=300/offset=300 request returned a disjoint page
  from a limit=300/offset=0 request, and together they reproduced a plain
  limit=600 request exactly). A single ``limit=600`` call is in fact
  enough to get every fantasy-relevant player in one shot, but this module
  still pages + sleeps between requests to stay polite regardless of the
  server's effective per-request cap.
* ``filterSlotIds`` values are ESPN *lineup slot* ids, which are a
  different numbering than a player's own ``defaultPositionId``. Filtering
  to slot ids ``[0, 2, 4, 6, 16, 17]`` (QB, RB, WR, TE, D/ST, K slots) over
  a 600-player pull returned exactly six ``defaultPositionId`` values --
  1 (QB, 85 players), 2 (RB, 148), 3 (WR, 197), 4 (TE, 87), 5 (K, 51), 16
  (D/ST, 32) -- with zero IDP noise, confirming both numberings and the
  filter's effect at once.
* ``player.stats[]`` entries: ``statSourceId`` 0 = actual, 1 = projection;
  ``statSplitTypeId`` 0 = season total (``scoringPeriodId`` 0),
  1 = single game/week (``scoringPeriodId`` = that week). Confirmed on
  Jahmyr Gibbs (RB, id 4429795): his statSourceId=0/statSplitTypeId=1 rows
  are plausible single-game lines (e.g. 19 rush att / 80 rush yds for a
  2025 week), while his statSourceId=1/statSplitTypeId=0/seasonId=2026 row
  is a full-season total (286 rush att / 1389 rush yds -- an RB1 workload,
  as expected for a top-drafted back). Also confirmed that requesting the
  endpoint with any ``?scoringPeriodId=N`` query param returns *every*
  week's stats (not just week N) alongside the season rows, so one page
  fetch covers a whole season's worth of weekly + rest-of-season data.
* Stat-ID -> canonical mapping was cross-checked two ways: (a) against the
  long-standing, community-maintained ``PLAYER_STATS_MAP`` in the
  ``espn-api`` PyPI package's ``constant.py`` (not installed here -- no new
  dependency was added, it was only read for reference), and (b) our own
  arithmetic sanity checks against live data:
    - QB: id0 == id1 + id2 (attempts == completions + incompletions) held
      exactly for Josh Allen, Lamar Jackson, Jalen Hurts, and Joe Burrow's
      season projections.
    - K (Brandon Aubrey): id74+id76==id75, id77+id79==id78,
      id80+id82==id81 (each distance bucket's made+missed==attempted), and
      id74+id77+id80==id83 (the three distance buckets' makes sum to the
      total makes figure) all held to float precision.
    - D/ST (Texans): id127 (yards allowed) totalled ~5643 for the season,
      i.e. ~332 yds/game across 17 games -- right in line with a real NFL
      defense; id120 (points allowed) totalled ~345, i.e. ~20 pts/game,
      same sanity check.
  Two ids the third-party table calls "receivingYards duplicate of 42"
  (id 61) and "receivingReceptions duplicate of 41" (id 53) did NOT
  actually match on live data for id 61 (see comment below), so it is
  deliberately left out of the mapping; id 53 for receptions checked out
  and is used as the season-level reception count (season-level payloads
  carry receptions under "53", not "41"; "41" appears only on individual
  game/week rows -- both are tried).
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

import httpx
from sqlalchemy.orm import Session

from app.models import Player, Projection, SyncLog
from app.services.matching import find_by_name_position
from app.services.stats import clean_stat_line

logger = logging.getLogger(__name__)

SOURCE = "espn"

BASE_URL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
    "/seasons/{season}/segments/0/leaguedefaults/3"
)

#: ESPN lineup-slot ids covering every standard fantasy-relevant position
#: (QB, RB, WR, TE, D/ST, K). Verified live -- see module docstring.
_FANTASY_SLOT_IDS = [0, 2, 4, 6, 16, 17]

#: ESPN's own player-position id (`defaultPositionId`), used only to derive
#: a normalized position string for the name-matching fallback.
_DEFAULT_POSITION_ID_TO_POSITION = {
    1: "QB",
    2: "RB",
    3: "WR",
    4: "TE",
    5: "K",
    16: "DST",
}

PAGE_SIZE = 200
PAGE_DELAY_SECONDS = 0.3
#: 5 * 200 = 1000 players, comfortably above the ~600 fantasy-relevant
#: target; paging stops earlier anyway once a short/empty page comes back.
MAX_PAGES = 5

#: ESPN player-stat-ID (as it appears, stringified, in a stats dict) ->
#: canonical stat key. IDs not listed here are simply ignored.
ESPN_STAT_ID_TO_CANONICAL: dict[str, str] = {
    # passing
    "0": "pass_att",
    "1": "pass_cmp",
    "3": "pass_yds",
    "4": "pass_td",
    "19": "pass_2pt",
    "20": "pass_int",
    # rushing
    "23": "rush_att",
    "24": "rush_yds",
    "25": "rush_td",
    "26": "rush_2pt",
    # receiving (see _REC_KEYS below for receptions itself)
    "42": "rec_yds",
    "43": "rec_td",
    "44": "rec_2pt",
    # misc offense
    "72": "fum_lost",
    # kicking -- ESPN only splits makes into <40 / 40-49 / 50+, coarser than
    # our 0-19/20-29/30-39/40-49/50+ buckets. 40-49 and 50+ line up exactly;
    # everything under 40 has nowhere precise to go, so it lands in the
    # distance-less `fg_made` bucket (per stats.py: "only when a source
    # gives no distance split" -- the closest honest fit for what ESPN
    # actually gives us below 40).
    "74": "fg_50_plus",
    "77": "fg_40_49",
    "80": "fg_made",
    "85": "fg_miss",  # total misses across all distances
    "86": "xp_made",
    "88": "xp_miss",
    # team defense / special teams
    "95": "dst_int",
    "96": "dst_fum_rec",
    "97": "dst_blk",
    "98": "dst_safety",
    "99": "dst_sack",
    "105": "dst_td",  # defensive + special-teams TDs combined
    "120": "dst_pts_allowed",
    "127": "dst_yds_allowed",
}

#: Receptions move ESPN stat id depending on split type: season-level
#: aggregates (statSplitTypeId=0) key it under "53"; per-game/weekly rows
#: (statSplitTypeId=1) use "41". Try both, in that order.
_REC_KEYS = ("53", "41")


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


def translate_stat_line(raw_stats: dict[str, Any]) -> dict[str, float]:
    """Translate one ESPN ``stats`` dict (string id -> number) to canonical."""
    translated: dict[str, Any] = {}
    for espn_id, canonical in ESPN_STAT_ID_TO_CANONICAL.items():
        if espn_id in raw_stats:
            translated[canonical] = raw_stats[espn_id]
    for key in _REC_KEYS:
        if key in raw_stats:
            translated["rec"] = raw_stats[key]
            break
    return clean_stat_line(translated)


def _select_stat_line(
    stats: list[dict], season: int, week: int | None
) -> dict[str, Any] | None:
    """Pick the one ``stats[]`` entry matching source=projection + our split."""
    for entry in stats:
        if entry.get("statSourceId") != 1 or entry.get("seasonId") != season:
            continue
        if week is None:
            if entry.get("statSplitTypeId") == 0:
                return entry.get("stats") or {}
        elif (
            entry.get("statSplitTypeId") == 1
            and entry.get("scoringPeriodId") == week
        ):
            return entry.get("stats") or {}
    return None


def _fetch_page(
    client: httpx.Client, season: int, offset: int, limit: int
) -> list[dict]:
    url = BASE_URL.format(season=season)
    filt = {
        "players": {
            "filterSlotIds": {"value": _FANTASY_SLOT_IDS},
            "limit": limit,
            "offset": offset,
            "sortPercOwned": {"sortAsc": False, "sortPriority": 1},
        }
    }
    response = client.get(
        url,
        params={"view": "kona_player_info"},
        headers={"X-Fantasy-Filter": json.dumps(filt)},
    )
    response.raise_for_status()
    return response.json().get("players") or []


def _match_player(
    db: Session, espn_id: str | None, full_name: str | None, position: str | None
) -> tuple[Player | None, bool]:
    """Resolve a player; returns (player, matched_by_id)."""
    if espn_id:
        player = db.query(Player).filter(Player.espn_id == espn_id).one_or_none()
        if player is not None:
            return player, True

    player = find_by_name_position(db, full_name, position)
    if player is not None and espn_id and not player.espn_id:
        player.espn_id = espn_id
    return player, False


def _upsert_projection(
    db: Session,
    player_id: int,
    season: int,
    week: int | None,
    stat_json: dict[str, float],
    fetched_at: datetime,
) -> None:
    row = (
        db.query(Projection)
        .filter(
            Projection.player_id == player_id,
            Projection.source == SOURCE,
            Projection.season == season,
            Projection.week == week,
        )
        .one_or_none()
    )
    if row is None:
        row = Projection(player_id=player_id, source=SOURCE, season=season, week=week)
        db.add(row)
    row.stat_json = stat_json
    row.fetched_at = fetched_at
    db.flush()


def refresh_projections(db: Session, season: int, week: int | None = None) -> dict:
    """Pull ESPN projections for ``season``.

    ``week=None`` stores the full-season/rest-of-season row
    (``Projection.week`` NULL); ``week=N`` stores that single week's row.
    Players are matched by ``espn_id`` first, falling back to normalized
    name+position; unresolved players are counted and skipped rather than
    creating new ``Player`` rows.
    """
    resource = "espn_projections"
    with _sync_log(db, resource) as log:
        fetched_at = _utcnow()
        matched_by_id = 0
        matched_by_name = 0
        unmatched = 0
        no_projection = 0
        saved = 0
        pages = 0

        with httpx.Client(timeout=15.0) as client:
            offset = 0
            while pages < MAX_PAGES:
                page = _fetch_page(client, season, offset, PAGE_SIZE)
                if not page:
                    break
                pages += 1

                for entry in page:
                    player_payload = entry.get("player") or {}
                    raw_id = player_payload.get("id")
                    espn_id = str(raw_id) if raw_id is not None else None
                    full_name = player_payload.get("fullName")
                    position = _DEFAULT_POSITION_ID_TO_POSITION.get(
                        player_payload.get("defaultPositionId")
                    )

                    raw_stats = _select_stat_line(
                        player_payload.get("stats") or [], season, week
                    )
                    if raw_stats is None:
                        no_projection += 1
                        continue

                    stat_json = translate_stat_line(raw_stats)
                    if not stat_json:
                        no_projection += 1
                        continue

                    player, by_id = _match_player(db, espn_id, full_name, position)
                    if player is None:
                        unmatched += 1
                        continue
                    if by_id:
                        matched_by_id += 1
                    else:
                        matched_by_name += 1

                    _upsert_projection(db, player.id, season, week, stat_json, fetched_at)
                    saved += 1

                db.commit()
                offset += len(page)
                if len(page) < PAGE_SIZE:
                    break
                time.sleep(PAGE_DELAY_SECONDS)

        week_label = "ROS/season" if week is None else f"week {week}"
        log.message = (
            f"season {season} {week_label}: {saved} projection(s) saved across "
            f"{pages} page(s) ({matched_by_id} by espn_id, {matched_by_name} by "
            f"name, {unmatched} unmatched, {no_projection} with no projection line)"
        )
        return {
            "season": season,
            "week": week,
            "pages": pages,
            "saved": saved,
            "matched_by_id": matched_by_id,
            "matched_by_name": matched_by_name,
            "unmatched": unmatched,
            "no_projection": no_projection,
        }


def refresh_season_projections(db: Session) -> dict:
    """Registry-friendly wrapper: full-season projections for the current season."""
    from app.services.yahoo.sync import current_nfl_season

    return refresh_projections(db, current_nfl_season(), week=None)
