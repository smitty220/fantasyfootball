"""FantasyPros public v2 API -- official player projections (requires a key).

``FANTASYPROS_API_KEY`` (``app.config.settings``) had not arrived as of
writing, so this module is built and tested against the *documented*
schema with a mocked ``x-api-key`` + response. Nothing here has been
exercised against the live API with real auth -- see the VERIFIED vs.
ASSUMED split below so the first real call is easy to sanity-check and fix.

VERIFIED on 2026-09-06, straight from the real, live OpenAPI spec at
https://api.fantasypros.com/public/v2/docs (which loads its spec from
https://api.fantasypros.com/public/v2/docs/fantasypros_v2_public.yml --
fetched and read directly, not guessed):

* Base URL: ``https://api.fantasypros.com/public/v2/json``
* Endpoint: ``GET /nfl/{season}/projections``
* Auth: ``x-api-key`` request header (OpenAPI ``apiKey`` security scheme).
* Query params actually on this endpoint: ``position`` (REQUIRED --
  one value from the ``NFLPositions`` enum: QB/RB/WR/TE/K/DST/... -- note
  this is the *singular* param; ``positions`` (plural, colon-delimited) is
  a separate, optional filter on the response and is not used here since
  we already loop one position at a time), ``week`` (integer; the spec's
  own words: "use week = 0 for preseason projections"), ``ros`` (boolean,
  "Return Rest of Season projections", default false), plus generic
  ``filters`` (expert-id filter, unused).
* Response body::

      {"season": "2025", "week": "0", "count": "194", "positions": "RB",
       "scoring": "STD", "experts": [9, 22, ...],
       "players": [{"fpid": "17240", "mflid": "13604",
                     "name": "Saquon Barkley", "position_id": "RB",
                     "team_id": "PHI", "filename": "saquon-barkley.php",
                     "stats": [ <position-specific stat object> ]}]}

  ``stats`` is documented as an array (``oneOf`` a QB / RB-WR-TE / K / DST
  / IDP schema); we read ``stats[0]``.
* Documented stat fields translated below, per position schema:
    - QB: pass_att, pass_cmp, pass_yds, pass_tds, pass_ints, rush_att,
      rush_yds, rush_tds, fumbles, ret_tds, 2pt_tds (+ points/points_ppr/
      points_half, not used -- we store raw stats, not source-computed
      fantasy points).
    - RB/WR/TE: rush_att, rush_yds, rush_tds, rec_rec, rec_yds, rec_tds,
      fumbles, ret_tds, 2pt_tds.
    - K: fga, fg, xpt (no distance splits at all in this schema).
    - DST: def_sack, def_int, def_td, def_pa_a..def_pa_g (7 points-allowed
      buckets), def_safety, def_ff, def_fr, def_retd (no total
      points-allowed or yards-allowed figure anywhere in this schema).
* The projections payload carries **no** ECR/expert-consensus-rank field
  per player -- only ``points``/``points_ppr``/``points_half`` plus raw
  stat counts. ECR ranks live on the separate ``/{sport}/{season}/rankings``
  and ``/consensus-rankings`` endpoints. Per the task, that's skipped here
  and just flagged: a real "pull FantasyPros ECR" feature needs one of
  those endpoints, not this one.

ASSUMPTIONS (the spec doesn't pin these down; flagged so they're easy to
revisit against a real response once the key exists):

* ``week=None`` (our ``Projection.week`` NULL == "rest-of-season/full
  season" convention) is sent as ``week=0``, i.e. the documented
  "preseason projections" full-season total -- NOT ``ros=true``, which
  FantasyPros treats as a distinct "remaining weeks from here" projection
  once a season is underway. If ``week=0`` stops returning sane
  full-season totals mid-season, switch this branch to ``ros=true``.
* ``fumbles`` (QB and RB/WR/TE schemas) is mapped straight to our
  canonical ``fum_lost``. The schema has no separate fumbles-committed vs.
  fumbles-lost split, so this may overcount vs. actual lost fumbles --
  revisit once real numbers are visible.
* ``2pt_tds`` is a single combined field (the schema doesn't say whether a
  QB's or a receiver's is thrown/run/caught). We map it QB -> ``pass_2pt``,
  RB -> ``rush_2pt``, WR/TE -> ``rec_2pt`` on the assumption that a given
  position's 2pt conversions are overwhelmingly of that position's own
  type; most scoring settings weight all three the same anyway, so a
  misclassification here is low-stakes even if wrong.
* DST's 7 points-allowed buckets (``def_pa_a``..``def_pa_g``) don't map to
  our single ``dst_pts_allowed``/``dst_yds_allowed`` canonical keys (which
  don't exist at all in this schema) -- left unmapped rather than guessing
  bucket boundaries.
* ``def_td`` + ``def_retd`` (documented as separate: defensive TDs vs.
  return TDs) are summed into one canonical ``dst_td``, mirroring how the
  ESPN ingester combines its own defensive + special-teams TD ids.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Player, Projection, SyncLog
from app.services.matching import find_by_name_position
from app.services.stats import clean_stat_line

logger = logging.getLogger(__name__)

SOURCE = "fantasypros"

BASE_URL = "https://api.fantasypros.com/public/v2/json"

#: Positions looped over one at a time, since `position` is a required,
#: single-valued query param on this endpoint (see module docstring).
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")


class FantasyProsNotConfiguredError(RuntimeError):
    """Raised when FANTASYPROS_API_KEY is empty; no request is attempted."""


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


def translate_stat_line(position: str, raw: dict[str, Any]) -> dict[str, float]:
    """Translate one FantasyPros position-specific stats object to canonical."""
    translated: dict[str, Any] = {}

    if position == "QB":
        translated.update(
            pass_att=raw.get("pass_att"),
            pass_cmp=raw.get("pass_cmp"),
            pass_yds=raw.get("pass_yds"),
            pass_td=raw.get("pass_tds"),
            pass_int=raw.get("pass_ints"),
            rush_att=raw.get("rush_att"),
            rush_yds=raw.get("rush_yds"),
            rush_td=raw.get("rush_tds"),
            fum_lost=raw.get("fumbles"),
            ret_td=raw.get("ret_tds"),
            pass_2pt=raw.get("2pt_tds"),  # ASSUMPTION -- see module docstring
        )
    elif position in ("RB", "WR", "TE"):
        translated.update(
            rush_att=raw.get("rush_att"),
            rush_yds=raw.get("rush_yds"),
            rush_td=raw.get("rush_tds"),
            rec=raw.get("rec_rec"),
            rec_yds=raw.get("rec_yds"),
            rec_td=raw.get("rec_tds"),
            fum_lost=raw.get("fumbles"),
            ret_td=raw.get("ret_tds"),
        )
        two_pt_key = "rush_2pt" if position == "RB" else "rec_2pt"
        translated[two_pt_key] = raw.get("2pt_tds")  # ASSUMPTION
    elif position == "K":
        translated.update(
            fg_made=raw.get("fg"),  # no distance split in this schema
            xp_made=raw.get("xpt"),
        )
    elif position == "DST":
        def_td = raw.get("def_td")
        def_retd = raw.get("def_retd")
        combined_td = None
        if def_td is not None or def_retd is not None:
            combined_td = (def_td or 0) + (def_retd or 0)
        translated.update(
            dst_sack=raw.get("def_sack"),
            dst_int=raw.get("def_int"),
            dst_fum_rec=raw.get("def_fr"),
            dst_safety=raw.get("def_safety"),
            dst_td=combined_td,
        )

    return clean_stat_line(translated)


def _match_player(
    db: Session, fp_id: str | None, full_name: str | None, position: str | None
) -> tuple[Player | None, bool]:
    """Resolve a player; returns (player, matched_by_id)."""
    if fp_id:
        player = db.query(Player).filter(Player.fantasypros_id == fp_id).one_or_none()
        if player is not None:
            return player, True

    player = find_by_name_position(db, full_name, position)
    if player is not None and fp_id and not player.fantasypros_id:
        player.fantasypros_id = fp_id
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


#: Pause between per-position requests; FantasyPros rate-limits per minute
#: (observed live: bursts of 6+ quick requests draw 429s).
REQUEST_PACING_SECONDS = 1.5

#: 429 retries: honor Retry-After when present, else this fallback wait.
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_FALLBACK_WAIT = 15.0


def _fetch_position(
    client: httpx.Client,
    season: int,
    position: str,
    week: int | None,
    ros: bool = False,
) -> list[dict]:
    params: dict[str, Any] = {"position": position}
    if ros:
        # FantasyPros' expert rest-of-season mode (news-aware, remaining games
        # only). Probed per refresh; empty until FP publishes it in-season.
        params["ros"] = "true"
    else:
        params["week"] = week if week is not None else 0

    for attempt in range(RATE_LIMIT_RETRIES + 1):
        response = client.get(f"{BASE_URL}/nfl/{season}/projections", params=params)
        if response.status_code == 429 and attempt < RATE_LIMIT_RETRIES:
            retry_after = response.headers.get("Retry-After")
            try:
                wait = float(retry_after) if retry_after else RATE_LIMIT_FALLBACK_WAIT
            except ValueError:
                wait = RATE_LIMIT_FALLBACK_WAIT
            wait = min(max(wait, 1.0), 60.0)
            logger.info(
                "FantasyPros rate limited (%s); retrying in %.0fs", position, wait
            )
            time.sleep(wait)
            continue
        response.raise_for_status()
        return response.json().get("players") or []
    raise RuntimeError("unreachable")  # pragma: no cover


def _season_fetch_mode(client: httpx.Client, season: int) -> bool:
    """True when FantasyPros' ``ros=true`` mode has data for this season.

    Probed once per season refresh with a single QB request. Empty (as before
    the season's first results exist, or if the key's tier lacks it) means we
    fall back to the ``week=0`` full-season totals, exactly as before.
    """
    try:
        return bool(_fetch_position(client, season, "QB", None, ros=True))
    except httpx.HTTPStatusError:
        return False


def refresh_projections(db: Session, season: int, week: int | None = None) -> dict:
    """Pull FantasyPros projections for ``season`` across QB/RB/WR/TE/K/DST.

    ``week=None`` stores the full-season/rest-of-season row
    (``Projection.week`` NULL); ``week=N`` stores that single week's row.
    Players are matched by ``fantasypros_id`` first, falling back to
    normalized name+position; unresolved players are counted and skipped
    rather than creating new ``Player`` rows.

    Raises `FantasyProsNotConfiguredError` (before making any request or
    writing any SyncLog row) if `FANTASYPROS_API_KEY` is unset.
    """
    if not settings.FANTASYPROS_API_KEY:
        raise FantasyProsNotConfiguredError(
            "FANTASYPROS_API_KEY is not set; cannot refresh FantasyPros projections."
        )

    resource = (
        "fantasypros_projections" if week is None else "fantasypros_week_projections"
    )
    with _sync_log(db, resource) as log:
        fetched_at = _utcnow()
        matched_by_id = 0
        matched_by_name = 0
        unmatched = 0
        no_projection = 0
        saved = 0
        per_position: dict[str, int] = {}

        with httpx.Client(
            timeout=15.0, headers={"x-api-key": settings.FANTASYPROS_API_KEY}
        ) as client:
            ros_mode = False
            if week is None:
                ros_mode = _season_fetch_mode(client, season)
                time.sleep(REQUEST_PACING_SECONDS)  # the probe counts for pacing
            for index, position in enumerate(POSITIONS):
                if index:
                    time.sleep(REQUEST_PACING_SECONDS)
                players = _fetch_position(client, season, position, week, ros=ros_mode)
                per_position[position] = len(players)

                for entry in players:
                    raw_fp_id = entry.get("fpid")
                    fp_id = str(raw_fp_id) if raw_fp_id is not None else None
                    full_name = entry.get("name")
                    entry_position = entry.get("position_id") or position

                    # Live API (verified 2026-09-06): "stats" is a dict of
                    # stat name -> value, not a list.
                    stats = entry.get("stats") or {}
                    if isinstance(stats, list):  # tolerate the shape we mocked
                        stats = stats[0] if stats else {}
                    if not stats:
                        no_projection += 1
                        continue

                    stat_json = translate_stat_line(entry_position, stats)
                    if not stat_json:
                        no_projection += 1
                        continue
                    if ros_mode:
                        # Meta flag (not a canonical stat, scores 0): tells the
                        # evaluator this row is already remaining-season-only,
                        # so schedule scaling must skip it.
                        stat_json["_remaining_only"] = True

                    player, by_id = _match_player(db, fp_id, full_name, entry_position)
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

        if week is None:
            week_label = (
                "ROS (expert rest-of-season mode)"
                if ros_mode
                else "season totals (ROS mode not yet published)"
            )
        else:
            week_label = f"week {week}"
        log.message = (
            f"season {season} {week_label}: {saved} projection(s) saved across "
            f"positions {per_position} ({matched_by_id} by fantasypros_id, "
            f"{matched_by_name} by name, {unmatched} unmatched, {no_projection} "
            "with no projection line)"
        )
        return {
            "season": season,
            "week": week,
            "saved": saved,
            "matched_by_id": matched_by_id,
            "matched_by_name": matched_by_name,
            "unmatched": unmatched,
            "no_projection": no_projection,
            "per_position": per_position,
        }


def refresh_season_projections(db: Session) -> dict:
    """Registry-friendly wrapper: full-season projections, current season."""
    from app.services.yahoo.sync import current_nfl_season

    return refresh_projections(db, current_nfl_season(), week=None)


def refresh_week_projections(db: Session) -> dict:
    """Registry-friendly wrapper: current-week projections (week from ESPN)."""
    from app.services.espn import current_week
    from app.services.yahoo.sync import current_nfl_season

    season = current_nfl_season()
    return refresh_projections(db, season, week=current_week(season))
