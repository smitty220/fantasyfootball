"""Pull Yahoo Fantasy league data into SQLite.

Every public entry point records a :class:`~app.models.SyncLog` row, and each
sub-resource of :func:`sync_league` gets its own log row so one flaky endpoint
(Yahoo's are undocumented and occasionally 500) degrades that slice of the sync
rather than aborting the whole league.
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterator

import yahoo_fantasy_api as yfa
from sqlalchemy.orm import Session

from app.models import (
    DraftPick,
    League,
    LeaguePlayer,
    Matchup,
    Player,
    RosterSlot,
    SyncLog,
    Team,
    Transaction,
)
from app.services.yahoo.session import make_session_context

logger = logging.getLogger(__name__)

NFL_GAME_CODE = "nfl"

#: Yahoo doles out players 25 at a time; be polite between pages.
PLAYERS_PER_PAGE = 25
PAGE_DELAY_SECONDS = 0.5
MAX_PLAYER_PAGES = 40

#: How many recent transactions to pull per sync.
TRANSACTION_COUNT = 50

TRANSACTION_TYPES = "add,drop,commish,trade"

#: Positions that describe a lineup slot rather than what a player *is*.
_SLOT_POSITIONS = {
    "BN",
    "IR",
    "IR+",
    "IR-R",
    "FLEX",
    "UTIL",
    "W/R",
    "W/T",
    "W/R/T",
    "Q/W/R/T",
    "NA",
}


class YahooSyncError(RuntimeError):
    """Raised when a sync cannot be started or completed at all."""


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def current_nfl_season(today: date | None = None) -> int:
    """The Yahoo NFL season currently in play.

    A season labelled Y runs Sep Y through early Jan Y+1, so anything before
    March belongs to the previous season's playoffs.
    """
    today = today or date.today()
    return today.year if today.month >= 3 else today.year - 1


def _to_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def _jsonable(value: Any) -> Any:
    """Round-trip through JSON so SQLAlchemy's JSON column never chokes."""
    return json.loads(json.dumps(value, default=str))


@contextmanager
def _sync_log(
    db: Session, resource: str, league_id: int | None = None
) -> Iterator[SyncLog]:
    """Bracket a unit of sync work with a ``sync_log`` row."""
    log = SyncLog(
        resource=resource,
        league_id=league_id,
        started_at=_utcnow(),
        status="running",
    )
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


def _run_step(
    db: Session,
    resource: str,
    league_id: int | None,
    fn: Callable[[], Any],
    errors: list[str],
) -> Any:
    """Run one sub-resource sync, logging and swallowing its failures."""
    try:
        with _sync_log(db, resource, league_id) as log:
            result = fn()
            if isinstance(result, str):
                log.message = result[:2000]
            return result
    except Exception as exc:  # noqa: BLE001 - one bad resource must not abort
        logger.exception("Yahoo sync step %s failed", resource)
        errors.append(f"{resource}: {exc}")
        return None


# --------------------------------------------------------------------------- #
# players
# --------------------------------------------------------------------------- #


def _player_name(yahoo_player: dict) -> str | None:
    name = yahoo_player.get("name")
    if isinstance(name, dict):
        return name.get("full")
    return name or None


def _primary_position(yahoo_player: dict) -> str | None:
    for key in ("primary_position", "display_position"):
        value = yahoo_player.get(key)
        if value:
            return str(value).split(",")[0].strip() or None

    for pos in yahoo_player.get("eligible_positions") or []:
        if isinstance(pos, dict):
            pos = pos.get("position")
        if pos and pos not in _SLOT_POSITIONS:
            return pos
    return None


def _bye_week(yahoo_player: dict) -> int | None:
    bye = yahoo_player.get("bye_weeks")
    if isinstance(bye, dict):
        return _to_int(bye.get("week"))
    return _to_int(yahoo_player.get("bye_week"))


def _get_or_create_player(db: Session, yahoo_player: dict) -> Player:
    """Resolve a Yahoo player payload to a canonical ``players`` row.

    Matches on ``yahoo_id``; other platform IDs stay NULL until the nflverse
    crosswalk enrichment lands.
    """
    raw_id = yahoo_player.get("player_id")
    yahoo_id = str(raw_id) if raw_id not in (None, "") else None

    player: Player | None = None
    if yahoo_id:
        player = db.query(Player).filter(Player.yahoo_id == yahoo_id).first()

    name = _player_name(yahoo_player)
    if player is None:
        player = Player(full_name=name or f"Yahoo player {yahoo_id}", yahoo_id=yahoo_id)
        db.add(player)
    elif name:
        player.full_name = name

    position = _primary_position(yahoo_player)
    if position:
        player.position = position

    nfl_team = yahoo_player.get("editorial_team_abbr")
    if nfl_team:
        player.nfl_team = nfl_team

    if "status" in yahoo_player:
        player.injury_status = yahoo_player.get("status") or None

    bye_week = _bye_week(yahoo_player)
    if bye_week:
        player.bye_week = bye_week

    player.updated_at = _utcnow()
    db.flush()
    return player


def _upsert_league_player(
    db: Session,
    league_id: int,
    player: Player,
    status: str,
    percent_owned: float | None = None,
    on_team_id: int | None = None,
) -> LeaguePlayer:
    row = (
        db.query(LeaguePlayer)
        .filter(
            LeaguePlayer.league_id == league_id,
            LeaguePlayer.player_id == player.id,
        )
        .one_or_none()
    )
    if row is None:
        row = LeaguePlayer(league_id=league_id, player_id=player.id)
        db.add(row)

    row.status = status
    if percent_owned is not None:
        row.percent_owned = percent_owned
    row.on_team_id = on_team_id
    row.synced_at = _utcnow()
    db.flush()
    return row


# --------------------------------------------------------------------------- #
# leagues
# --------------------------------------------------------------------------- #


def _upsert_league(db: Session, league_key: str, lg_settings: dict) -> League:
    league = db.query(League).filter(League.league_key == league_key).one_or_none()
    if league is None:
        league = League(league_key=league_key)
        db.add(league)

    league.game_key = str(lg_settings.get("game_key") or league_key.split(".")[0])
    league.name = lg_settings.get("name") or league.name or league_key
    league.season = _to_int(lg_settings.get("season")) or league.season or 0
    league.num_teams = _to_int(lg_settings.get("num_teams")) or league.num_teams
    league.scoring_type = lg_settings.get("scoring_type") or league.scoring_type
    league.current_week = _to_int(lg_settings.get("current_week")) or league.current_week
    league.settings_json = _jsonable(lg_settings)
    league.synced_at = _utcnow()
    # is_keeper is deliberately untouched: the owner flags keeper leagues by hand.
    db.flush()
    return league


def discover_leagues(db: Session) -> list[League]:
    """Find the logged-in user's NFL leagues for the current season."""
    season = current_nfl_season()
    with _sync_log(db, "yahoo.discover_leagues") as log:
        sc = make_session_context(db)
        try:
            game = yfa.Game(sc, NFL_GAME_CODE)
            league_keys = game.league_ids(
                game_codes=[NFL_GAME_CODE], seasons=[str(season)]
            )

            leagues: list[League] = []
            for league_key in league_keys:
                lg = game.to_league(league_key)
                leagues.append(_upsert_league(db, league_key, lg.settings()))
            db.commit()

            log.message = f"season {season}: {len(leagues)} league(s)"
            return leagues
        finally:
            sc.close()


# --------------------------------------------------------------------------- #
# per-league sub-resources
# --------------------------------------------------------------------------- #


def _manager_name(team_data: dict) -> str | None:
    managers = team_data.get("managers") or []
    for entry in managers:
        manager = entry.get("manager") if isinstance(entry, dict) else None
        if isinstance(manager, dict):
            name = manager.get("nickname") or manager.get("email")
            if name:
                return name
    return None


def _logo_url(team_data: dict) -> str | None:
    logos = team_data.get("team_logos") or []
    for entry in logos:
        logo = entry.get("team_logo") if isinstance(entry, dict) else None
        if isinstance(logo, dict) and logo.get("url"):
            return logo["url"]
    return None


def _sync_settings(db: Session, league_key: str, lg) -> League:
    league = _upsert_league(db, league_key, lg.settings())
    db.commit()
    return league


def _sync_teams(db: Session, league: League, lg) -> str:
    teams_data = lg.teams()
    standings = {row.get("team_key"): row for row in lg.standings()}

    my_team_key = next(
        (
            key
            for key, data in teams_data.items()
            if _truthy(data.get("is_owned_by_current_login"))
        ),
        None,
    )
    if my_team_key is None:
        try:
            my_team_key = lg.team_key()
        except Exception:  # noqa: BLE001 - best effort only
            logger.warning("Could not determine the logged-in user's team key")

    for team_key, data in teams_data.items():
        if not team_key:
            continue
        standing = standings.get(team_key, {})
        outcome = standing.get("outcome_totals") or {}

        team = db.query(Team).filter(Team.team_key == team_key).one_or_none()
        if team is None:
            team = Team(team_key=team_key, league_id=league.id, name="")
            db.add(team)

        team.league_id = league.id
        team.name = data.get("name") or team.name or team_key
        team.manager_name = _manager_name(data) or team.manager_name
        team.is_my_team = team_key == my_team_key
        team.wins = _to_int(outcome.get("wins"))
        team.losses = _to_int(outcome.get("losses"))
        team.ties = _to_int(outcome.get("ties"))
        team.rank = _to_int(standing.get("rank"))
        team.points_for = _to_float(standing.get("points_for"))
        team.points_against = _to_float(standing.get("points_against"))
        team.logo_url = _logo_url(data) or team.logo_url

    db.commit()
    return f"{len(teams_data)} team(s)"


def _sync_rosters(db: Session, league: League, lg, week: int) -> str:
    """Delete-and-replace roster slots for ``week``; also flag taken players."""
    teams = db.query(Team).filter(Team.league_id == league.id).all()
    slot_count = 0

    for team in teams:
        roster = lg.to_team(team.team_key).roster(week=week)

        db.query(RosterSlot).filter(
            RosterSlot.team_id == team.id, RosterSlot.week == week
        ).delete(synchronize_session=False)

        seen: set[int] = set()
        for entry in roster:
            player = _get_or_create_player(db, entry)
            if player.id in seen:
                continue
            seen.add(player.id)

            db.add(
                RosterSlot(
                    team_id=team.id,
                    week=week,
                    player_id=player.id,
                    selected_position=entry.get("selected_position"),
                )
            )
            _upsert_league_player(db, league.id, player, "T", on_team_id=team.id)
            slot_count += 1

        db.commit()

    return f"{slot_count} roster slot(s) across {len(teams)} team(s)"


def _parse_matchups(raw: dict) -> list[dict]:
    """Flatten Yahoo's scoreboard JSON into simple matchup dicts."""
    league_node = raw.get("fantasy_content", {}).get("league")
    scoreboard: dict | None = None
    if isinstance(league_node, list):
        for chunk in league_node:
            if isinstance(chunk, dict) and "scoreboard" in chunk:
                scoreboard = chunk["scoreboard"]
                break
    if not isinstance(scoreboard, dict):
        return []

    matchups = scoreboard.get("matchups")
    if matchups is None and isinstance(scoreboard.get("0"), dict):
        matchups = scoreboard["0"].get("matchups")
    if not isinstance(matchups, dict):
        return []

    parsed: list[dict] = []
    for key, wrapper in matchups.items():
        if key == "count" or not isinstance(wrapper, dict):
            continue
        matchup = wrapper.get("matchup")
        if not isinstance(matchup, dict):
            continue

        teams_node = matchup.get("teams")
        if teams_node is None and isinstance(matchup.get("0"), dict):
            teams_node = matchup["0"].get("teams")
        if not isinstance(teams_node, dict):
            continue

        teams: list[tuple[str | None, float | None]] = []
        for team_key, team_wrapper in teams_node.items():
            if team_key == "count" or not isinstance(team_wrapper, dict):
                continue
            teams.append(_parse_matchup_team(team_wrapper.get("team")))

        parsed.append(
            {
                "week": _to_int(matchup.get("week")),
                "status": matchup.get("status"),
                "is_playoffs": _truthy(matchup.get("is_playoffs")),
                "teams": teams,
            }
        )
    return parsed


def _parse_matchup_team(team_node: Any) -> tuple[str | None, float | None]:
    team_key: str | None = None
    points: float | None = None

    def visit(node: Any) -> None:
        nonlocal team_key, points
        if isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            if "team_key" in node and isinstance(node["team_key"], str):
                team_key = node["team_key"]
            team_points = node.get("team_points")
            if isinstance(team_points, dict):
                points = _to_float(team_points.get("total"))

    visit(team_node)
    return team_key, points


def _sync_matchups(db: Session, league: League, lg, week: int) -> str:
    parsed = _parse_matchups(lg.matchups(week=week))
    team_ids = {
        team.team_key: team.id
        for team in db.query(Team).filter(Team.league_id == league.id).all()
    }

    saved = 0
    for entry in parsed:
        teams = entry["teams"]
        if not teams:
            continue
        home_key, home_points = teams[0]
        away_key, away_points = teams[1] if len(teams) > 1 else (None, None)

        home_id = team_ids.get(home_key)
        if home_id is None:
            continue
        away_id = team_ids.get(away_key)
        matchup_week = entry["week"] or week

        row = (
            db.query(Matchup)
            .filter(
                Matchup.league_id == league.id,
                Matchup.week == matchup_week,
                Matchup.home_team_id == home_id,
                Matchup.away_team_id == away_id,
            )
            .one_or_none()
        )
        if row is None:
            row = Matchup(
                league_id=league.id,
                week=matchup_week,
                home_team_id=home_id,
                away_team_id=away_id,
            )
            db.add(row)

        row.home_points = home_points
        row.away_points = away_points
        row.is_playoffs = entry["is_playoffs"]
        row.status = entry["status"]
        saved += 1

    db.commit()
    return f"{saved} matchup(s) for week {week}"


def _iter_player_pages(lg, status: str) -> Iterator[list[dict]]:
    """Page through a league's player collection, 25 at a time.

    Uses the library's own page parser (``_players_from_page``) so we inherit
    its handling of Yahoo's unstable array shapes, but drive the paging
    ourselves so we can sleep between requests.
    """
    index = 0
    for _ in range(MAX_PLAYER_PAGES):
        raw = lg.yhandler.get_players_raw(lg.league_id, index, status, position=None)
        page_size, players = lg._players_from_page(raw)
        if not players:
            return

        yield players

        page_size = int(page_size)
        index += page_size
        if page_size % PLAYERS_PER_PAGE != 0:
            return
        time.sleep(PAGE_DELAY_SECONDS)


def _sync_free_agents(db: Session, league: League, lg) -> str:
    total = 0
    for status in ("FA", "W"):
        for page in _iter_player_pages(lg, status):
            for entry in page:
                player = _get_or_create_player(db, entry)
                _upsert_league_player(
                    db,
                    league.id,
                    player,
                    status,
                    percent_owned=_to_float(entry.get("percent_owned")),
                    on_team_id=None,
                )
                total += 1
            db.commit()
    return f"{total} available player(s)"


def _resolve_missing_players(db: Session, lg, yahoo_ids: list[int]) -> None:
    """Best-effort lookup of players we've never seen (drafted then dropped)."""
    for start in range(0, len(yahoo_ids), PLAYERS_PER_PAGE):
        chunk = yahoo_ids[start : start + PLAYERS_PER_PAGE]
        try:
            details = lg.player_details(chunk)
        except Exception:  # noqa: BLE001 - leave the pick's player_id NULL
            logger.warning("Could not resolve Yahoo player details for %s", chunk)
            continue
        for entry in details:
            _get_or_create_player(db, entry)
        db.commit()
        time.sleep(PAGE_DELAY_SECONDS)


def _sync_draft(db: Session, league: League, lg) -> str:
    results = lg.draft_results()
    if not results:
        return "no draft results"

    team_ids = {
        team.team_key: team.id
        for team in db.query(Team).filter(Team.league_id == league.id).all()
    }

    known = {
        yahoo_id
        for (yahoo_id,) in db.query(Player.yahoo_id).filter(
            Player.yahoo_id.isnot(None)
        )
    }
    missing = sorted(
        {
            int(r["player_id"])
            for r in results
            if r.get("player_id") is not None and str(r["player_id"]) not in known
        }
    )
    if missing:
        _resolve_missing_players(db, lg, missing)

    saved = 0
    for result in results:
        round_ = _to_int(result.get("round"))
        pick = _to_int(result.get("pick"))
        if round_ is None or pick is None:
            continue

        player_id = None
        raw_player_id = result.get("player_id")
        if raw_player_id is not None:
            player = (
                db.query(Player)
                .filter(Player.yahoo_id == str(raw_player_id))
                .first()
            )
            player_id = player.id if player else None

        row = (
            db.query(DraftPick)
            .filter(
                DraftPick.league_id == league.id,
                DraftPick.round == round_,
                DraftPick.pick == pick,
            )
            .one_or_none()
        )
        if row is None:
            row = DraftPick(league_id=league.id, round=round_, pick=pick)
            db.add(row)

        row.team_id = team_ids.get(result.get("team_key"))
        row.player_id = player_id
        row.cost = _to_int(result.get("cost"))
        saved += 1

    db.commit()
    return f"{saved} draft pick(s)"


def _sync_transactions(db: Session, league: League, lg) -> str:
    results = lg.transactions(TRANSACTION_TYPES, str(TRANSACTION_COUNT))

    saved = 0
    for entry in results:
        key = entry.get("transaction_key")
        if not key:
            continue

        row = (
            db.query(Transaction)
            .filter(Transaction.yahoo_transaction_key == key)
            .one_or_none()
        )
        if row is None:
            row = Transaction(yahoo_transaction_key=key, league_id=league.id)
            db.add(row)

        row.league_id = league.id
        row.type = entry.get("type")
        timestamp = _to_int(entry.get("timestamp"))
        if timestamp:
            row.timestamp = datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(
                tzinfo=None
            )
        row.data_json = _jsonable(entry)
        saved += 1

    db.commit()
    return f"{saved} transaction(s)"


# --------------------------------------------------------------------------- #
# league sync entry point
# --------------------------------------------------------------------------- #


def sync_league(db: Session, league_key: str) -> dict:
    """Sync one league end-to-end.

    Sub-resources are synced in dependency order; a failure in any one of them
    is logged and the rest still run.
    """
    existing = db.query(League).filter(League.league_key == league_key).one_or_none()
    errors: list[str] = []

    with _sync_log(
        db, "yahoo.league", existing.id if existing else None
    ) as log:
        sc = make_session_context(db)
        try:
            game = yfa.Game(sc, NFL_GAME_CODE)
            lg = game.to_league(league_key)

            league = _run_step(
                db,
                "yahoo.settings",
                existing.id if existing else None,
                lambda: _sync_settings(db, league_key, lg),
                errors,
            )
            if league is None:
                league = existing
            if league is None:
                raise YahooSyncError(
                    f"Could not load settings for league {league_key}; nothing to sync."
                )

            log.league_id = league.id
            week = league.current_week or 1

            _run_step(
                db, "yahoo.teams", league.id, lambda: _sync_teams(db, league, lg), errors
            )
            _run_step(
                db,
                "yahoo.rosters",
                league.id,
                lambda: _sync_rosters(db, league, lg, week),
                errors,
            )
            _run_step(
                db,
                "yahoo.matchups",
                league.id,
                lambda: _sync_matchups(db, league, lg, week),
                errors,
            )
            _run_step(
                db,
                "yahoo.free_agents",
                league.id,
                lambda: _sync_free_agents(db, league, lg),
                errors,
            )
            _run_step(
                db, "yahoo.draft", league.id, lambda: _sync_draft(db, league, lg), errors
            )
            _run_step(
                db,
                "yahoo.transactions",
                league.id,
                lambda: _sync_transactions(db, league, lg),
                errors,
            )

            league.synced_at = _utcnow()
            db.commit()

            log.status = "partial" if errors else "success"
            log.message = ("; ".join(errors))[:2000] if errors else f"week {week}"

            return {
                "league_key": league_key,
                "league_id": league.id,
                "week": week,
                "status": log.status,
                "errors": errors,
            }
        finally:
            sc.close()


def check_api_access(db: Session) -> str:
    """Probe whether Yahoo's Fantasy API gate is open for our app.

    Yahoo's 2026 access program approves apps asynchronously; until the
    approval binds to the consumer key, every Fantasy endpoint answers
    ``additional_authorization_required``. This cheap probe (one request to
    the public NFL game resource) logs the current state so the Data page
    shows the moment access goes live.
    """
    with _sync_log(db, "yahoo_access_check") as log:
        from app.services.yahoo.session import make_session_context

        with make_session_context(db) as sc:
            response = sc.session.get(
                "https://fantasysports.yahooapis.com/fantasy/v2/game/nfl",
                params={"format": "json"},
            )
        if response.status_code == 200:
            log.message = "ACCESS LIVE - Yahoo Fantasy API responds; run league discovery!"
        else:
            body = response.content.decode("utf-8", "replace")[:200]
            log.status = "error"
            if "additional_authorization_required" in body:
                log.message = (
                    "Approval gate still closed (additional_authorization_required); "
                    "waiting on Yahoo to activate the app."
                )
            else:
                log.message = f"HTTP {response.status_code}: {body}"
        return log.message
