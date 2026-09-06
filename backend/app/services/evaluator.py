"""Evaluation engine: free-agent evaluator + trade evaluator.

Everything here reads only from SQLite (see design.md: evaluators never call
external APIs). The two entry points are :func:`evaluate_free_agents` and
:func:`evaluate_trade`; the helpers above them are deliberately small and
mostly pure so the maths can be unit-tested without a database.

Core ideas
----------
*Rest-of-season points* come from the best available season-long projection
(``week IS NULL``) scored under the league's own rules, so the same player is
worth different amounts in the keeper league vs the redraft league.

*VOR* (value over replacement) subtracts a positional replacement level, which
is derived from how many starters the whole league needs at that position:
direct roster slots plus a share of each FLEX slot.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy.orm import Session

from app.models import (
    League,
    LeaguePlayer,
    Player,
    Projection,
    Team,
    TradeValue,
    TrendingSignal,
)
from app.services import scoring
from app.services.yahoo.sync import current_nfl_season

#: Projection sources we trust, best first.
SOURCE_PRIORITY: tuple[str, ...] = ("fantasypros", "espn")

#: Positions that can start in a standard lineup.
STARTABLE_POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE", "K", "DEF")

#: Positions eligible for a FLEX slot.
FLEX_POSITIONS: tuple[str, ...] = ("RB", "WR", "TE")

#: How one FLEX slot's startable demand is split across the eligible positions.
FLEX_WEIGHTS: dict[str, float] = {"RB": 0.4, "WR": 0.5, "TE": 0.1}

#: Roster slots that never start.
BENCH_SLOTS: frozenset[str] = frozenset({"BN", "IR", "NA"})

#: Slot label aliases seen in Yahoo/manual settings.
_SLOT_ALIASES: dict[str, str] = {
    "W/R/T": "FLEX",
    "W/R": "FLEX",
    "WR/RB": "FLEX",
    "WR/RB/TE": "FLEX",
    "WRT": "FLEX",
    "W/T": "FLEX",
    "DST": "DEF",
    "D/ST": "DEF",
    "PK": "K",
}

TRADE_VALUE_SOURCE = "fantasycalc"

#: Verdict is "fair" when the two sides' market values are within this fraction.
FAIR_MARGIN = 0.10

#: Games in a fantasy regular season + playoffs; used for the points-per-game
#: display only.
GAMES_PER_SEASON = 17


class TradeValidationError(ValueError):
    """A trade request that cannot be evaluated (bad team, bad player, ...)."""


# --- projections -----------------------------------------------------------


def best_projection(
    db: Session, player_id: int, season: int
) -> Projection | None:
    """Best season-long (``week IS NULL``) projection for one player.

    Sources are preferred in :data:`SOURCE_PRIORITY` order.
    """
    rows = (
        db.query(Projection)
        .filter(
            Projection.player_id == player_id,
            Projection.season == season,
            Projection.week.is_(None),
            Projection.source.in_(SOURCE_PRIORITY),
        )
        .all()
    )
    return _pick_best(rows)


def _pick_best(rows: Iterable[Projection]) -> Projection | None:
    by_source = {row.source: row for row in rows}
    for source in SOURCE_PRIORITY:
        if source in by_source:
            return by_source[source]
    return None


def league_points(
    projection: Projection | None, rules: scoring.ScoringRules
) -> float:
    """Fantasy points for a projection under one league's scoring rules."""
    if projection is None or not projection.stat_json:
        return 0.0
    return scoring.score_stat_line(projection.stat_json, rules)


def _projections_for(
    db: Session, season: int, player_ids: Sequence[int] | None = None
) -> dict[int, Projection]:
    """Best season-long projection per player, in one query."""
    query = db.query(Projection).filter(
        Projection.season == season,
        Projection.week.is_(None),
        Projection.source.in_(SOURCE_PRIORITY),
    )
    if player_ids is not None:
        if not player_ids:
            return {}
        query = query.filter(Projection.player_id.in_(list(player_ids)))

    grouped: dict[int, list[Projection]] = {}
    for row in query.all():
        grouped.setdefault(row.player_id, []).append(row)

    best: dict[int, Projection] = {}
    for player_id, rows in grouped.items():
        chosen = _pick_best(rows)
        if chosen is not None:
            best[player_id] = chosen
    return best


# --- roster shape ----------------------------------------------------------


def _normalize_slot(slot: str) -> str:
    label = (slot or "").strip().upper()
    return _SLOT_ALIASES.get(label, label)


def league_roster_slots(league: League) -> dict[str, int]:
    """The league's roster slot counts, falling back to the standard preset."""
    settings: Mapping[str, Any] = league.settings_json or {}
    slots = settings.get("roster_slots") or scoring.DEFAULT_ROSTER_SLOTS
    return {str(k): int(v) for k, v in dict(slots).items()}


def starter_slots(roster_slots: Mapping[str, int]) -> dict[str, float]:
    """League-wide startable demand per position, per team.

    Direct slots count fully; each FLEX slot is split across the eligible
    positions using :data:`FLEX_WEIGHTS`. Bench/IR slots are ignored.

    ``{QB:1, RB:2, WR:3, TE:1, FLEX:1, K:1, DEF:1}`` becomes
    ``{QB:1, RB:2.4, WR:3.5, TE:1.1, K:1, DEF:1}``.
    """
    demand: dict[str, float] = {}
    flex_count = 0

    for raw_slot, count in roster_slots.items():
        slot = _normalize_slot(raw_slot)
        if slot in BENCH_SLOTS or not count:
            continue
        if slot == "FLEX":
            flex_count += int(count)
        elif slot in STARTABLE_POSITIONS:
            demand[slot] = demand.get(slot, 0.0) + float(count)

    if flex_count:
        for position, weight in FLEX_WEIGHTS.items():
            demand[position] = demand.get(position, 0.0) + weight * flex_count

    return {position: round(value, 4) for position, value in demand.items()}


# --- replacement level -----------------------------------------------------


def replacement_levels(db: Session, league: League, season: int | None = None) -> dict[str, float]:
    """Points of the first non-startable player at each position.

    Every player with a projection (rostered or free agent) is ranked by league
    points; the replacement level is the player one spot past the last
    league-wide starter, i.e. rank ``ceil(starter_slots[pos] * num_teams) + 1``.

    A position with no projected players at all gets 0. If the pool is too
    shallow to reach that rank we use the worst projected player at the
    position rather than 0, which keeps VOR meaningful on tiny/partial pools.
    """
    season = season if season is not None else current_nfl_season()
    rules = scoring.league_rules(league.settings_json)
    demand = starter_slots(league_roster_slots(league))
    num_teams = league.num_teams or 12

    pools: dict[str, list[float]] = {position: [] for position in STARTABLE_POSITIONS}

    rows = (
        db.query(Projection, Player)
        .join(Player, Player.id == Projection.player_id)
        .filter(
            Projection.season == season,
            Projection.week.is_(None),
            Projection.source.in_(SOURCE_PRIORITY),
        )
        .all()
    )
    grouped: dict[int, tuple[str | None, list[Projection]]] = {}
    for projection, player in rows:
        position, projections = grouped.setdefault(player.id, (player.position, []))
        projections.append(projection)

    for position, projections in grouped.values():
        if position not in pools:
            continue
        pools[position].append(league_points(_pick_best(projections), rules))

    levels: dict[str, float] = {}
    for position, points in pools.items():
        if not points:
            levels[position] = 0.0
            continue
        points.sort(reverse=True)
        rank = math.ceil(demand.get(position, 0.0) * num_teams) + 1
        index = min(rank, len(points)) - 1
        levels[position] = round(points[index], 2)
    return levels


# --- lineups ---------------------------------------------------------------


def _fill_lineup(
    roster_slots: Mapping[str, int],
    points: Mapping[int, float],
    positions: Mapping[int, str | None],
    player_ids: Iterable[int],
) -> list[tuple[str, int]]:
    """Greedy optimal lineup: (slot, player_id) pairs.

    Fixed position slots are filled first with the best remaining player at
    that exact position, then FLEX slots take the best remaining RB/WR/TE.
    Because fixed slots accept only one position, filling them greedily before
    FLEX is optimal for this slot structure.
    """
    available = sorted(
        set(player_ids),
        key=lambda pid: (-points.get(pid, 0.0), pid),
    )
    used: set[int] = set()
    lineup: list[tuple[str, int]] = []

    counts: dict[str, int] = {}
    for raw_slot, count in roster_slots.items():
        slot = _normalize_slot(raw_slot)
        if slot in BENCH_SLOTS or not count:
            continue
        if slot == "FLEX" or slot in STARTABLE_POSITIONS:
            counts[slot] = counts.get(slot, 0) + int(count)

    def take(eligible: tuple[str, ...]) -> int | None:
        for pid in available:
            if pid in used:
                continue
            if positions.get(pid) in eligible:
                used.add(pid)
                return pid
        return None

    for position in STARTABLE_POSITIONS:
        for _ in range(counts.get(position, 0)):
            pid = take((position,))
            if pid is None:
                break
            lineup.append((position, pid))

    for _ in range(counts.get("FLEX", 0)):
        pid = take(FLEX_POSITIONS)
        if pid is None:
            break
        lineup.append(("FLEX", pid))

    return lineup


def optimal_lineup_points(
    db: Session,
    league: League,
    player_ids: Sequence[int],
    season: int | None = None,
) -> float:
    """Points of the best legal lineup buildable from ``player_ids``.

    Players without a projection are still eligible to fill a slot but
    contribute 0.
    """
    season = season if season is not None else current_nfl_season()
    points, positions = _points_and_positions(db, league, player_ids, season)
    lineup = _fill_lineup(league_roster_slots(league), points, positions, player_ids)
    return round(sum(points.get(pid, 0.0) for _slot, pid in lineup), 2)


def _points_and_positions(
    db: Session, league: League, player_ids: Sequence[int], season: int
) -> tuple[dict[int, float], dict[int, str | None]]:
    ids = list({int(pid) for pid in player_ids})
    if not ids:
        return {}, {}
    rules = scoring.league_rules(league.settings_json)
    projections = _projections_for(db, season, ids)
    points = {
        pid: league_points(projections.get(pid), rules) for pid in ids
    }
    positions = {
        player.id: player.position
        for player in db.query(Player).filter(Player.id.in_(ids)).all()
    }
    return points, positions


# --- free agents -----------------------------------------------------------


def _my_team(db: Session, league: League) -> Team | None:
    return (
        db.query(Team)
        .filter(Team.league_id == league.id, Team.is_my_team.is_(True))
        .first()
    )


def _team_player_ids(db: Session, league: League, team_id: int) -> list[int]:
    return [
        row.player_id
        for row in db.query(LeaguePlayer)
        .filter(
            LeaguePlayer.league_id == league.id,
            LeaguePlayer.on_team_id == team_id,
        )
        .all()
    ]


def _worst_starter_points(
    db: Session, league: League, team: Team, season: int
) -> dict[str, float]:
    """Worst projected starter points per position for one team.

    FLEX-eligible positions (RB/WR/TE) all map to the same figure: the worst
    starter among the team's RB/WR/TE starters, because any of them could be
    the one a new FLEX-eligible player displaces.
    """
    roster = _team_player_ids(db, league, team.id)
    if not roster:
        return {}

    points, positions = _points_and_positions(db, league, roster, season)
    lineup = _fill_lineup(league_roster_slots(league), points, positions, roster)

    by_position: dict[str, list[float]] = {}
    flex_pool: list[float] = []
    for _slot, pid in lineup:
        position = positions.get(pid)
        if position is None:
            continue
        by_position.setdefault(position, []).append(points.get(pid, 0.0))
        if position in FLEX_POSITIONS:
            flex_pool.append(points.get(pid, 0.0))

    worst = {
        position: min(values) for position, values in by_position.items() if values
    }
    if flex_pool:
        flex_worst = min(flex_pool)
        for position in FLEX_POSITIONS:
            worst[position] = flex_worst
    return worst


def _free_agent_players(
    db: Session, league: League, position: str | None
) -> list[Player]:
    if league.source == "manual":
        # Manual leagues have no FA rows: anything with no LeaguePlayer row in
        # this league at all is implicitly available.
        rostered = db.query(LeaguePlayer.player_id).filter(
            LeaguePlayer.league_id == league.id
        )
        query = db.query(Player).filter(
            Player.position.in_(STARTABLE_POSITIONS),
            ~Player.id.in_(rostered),
        )
    else:
        query = (
            db.query(Player)
            .join(LeaguePlayer, LeaguePlayer.player_id == Player.id)
            .filter(
                LeaguePlayer.league_id == league.id,
                LeaguePlayer.status.in_(("FA", "W")),
            )
        )

    if position:
        query = query.filter(Player.position == position.upper())
    return query.all()


def evaluate_free_agents(
    db: Session,
    league: League,
    position: str | None = None,
    limit: int = 50,
    season: int | None = None,
) -> list[dict]:
    """Rank a league's available players by value over replacement."""
    season = season if season is not None else current_nfl_season()
    rules = scoring.league_rules(league.settings_json)

    players = _free_agent_players(db, league, position)
    if not players:
        return []

    player_ids = [player.id for player in players]
    projections = _projections_for(db, season, player_ids)
    levels = replacement_levels(db, league, season)

    values = {
        row.player_id: row.value
        for row in db.query(TradeValue)
        .filter(
            TradeValue.player_id.in_(player_ids),
            TradeValue.source == TRADE_VALUE_SOURCE,
            TradeValue.format == "redraft",
        )
        .all()
    }
    trending = {
        row.player_id: row.count
        for row in db.query(TrendingSignal)
        .filter(
            TrendingSignal.player_id.in_(player_ids),
            TrendingSignal.source == "sleeper",
            TrendingSignal.kind == "add",
        )
        .all()
    }

    team = _my_team(db, league)
    worst_starters = (
        _worst_starter_points(db, league, team, season) if team is not None else {}
    )

    rows: list[dict] = []
    for player in players:
        projection = projections.get(player.id)
        ros_points = league_points(projection, rules)
        replacement = levels.get(player.position or "", 0.0)

        delta = None
        if team is not None and player.position in worst_starters:
            delta = round(ros_points - worst_starters[player.position], 1)

        rows.append(
            {
                "player_id": player.id,
                "full_name": player.full_name,
                "position": player.position,
                "nfl_team": player.nfl_team,
                "injury_status": player.injury_status,
                "has_projection": projection is not None,
                "ros_points": round(ros_points, 2),
                "ppg": round(ros_points / GAMES_PER_SEASON, 1),
                "vor": round(ros_points - replacement, 1),
                "trade_value": values.get(player.id),
                "trending_add": trending.get(player.id),
                "my_worst_starter_delta": delta,
            }
        )

    rows.sort(key=lambda row: (not row["has_projection"], -row["vor"], row["full_name"]))
    return rows[:limit]


# --- trades ----------------------------------------------------------------


def _side_input(side: Mapping[str, Any], label: str) -> tuple[int, list[int]]:
    try:
        team_id = int(side["team_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TradeValidationError(f"Side {label} is missing a valid team_id") from exc

    raw_ids = side.get("player_ids") or []
    player_ids = [int(pid) for pid in raw_ids]
    if not player_ids:
        raise TradeValidationError(f"Side {label} must include at least one player")
    if len(set(player_ids)) != len(player_ids):
        raise TradeValidationError(f"Side {label} lists the same player twice")
    return team_id, player_ids


def _load_team(db: Session, league: League, team_id: int, label: str) -> Team:
    team = db.query(Team).filter(Team.id == team_id).one_or_none()
    if team is None or team.league_id != league.id:
        raise TradeValidationError(
            f"Team {team_id} (side {label}) is not in league {league.league_key}"
        )
    return team


def _verdict(value_a: float, value_b: float) -> tuple[str, float]:
    """Verdict + margin from the market value each side *sends*.

    ``value_a`` is what team A gives up, ``value_b`` what team B gives up, so
    "favors_a" means team A comes out ahead: it receives more value
    (``value_b``) than it sends (``value_a``).
    """
    biggest = max(value_a, value_b)
    if biggest <= 0:
        return "fair", 0.0
    margin = abs(value_a - value_b) / biggest
    if margin <= FAIR_MARGIN:
        return "fair", round(margin, 4)
    return ("favors_a" if value_b > value_a else "favors_b"), round(margin, 4)


def evaluate_trade(
    db: Session,
    league: League,
    side_a: Mapping[str, Any],
    side_b: Mapping[str, Any],
    season: int | None = None,
) -> dict:
    """Score a two-team trade.

    ``side_a``/``side_b`` are ``{"team_id": int, "player_ids": [int]}``: the
    players each side *sends away*. The verdict describes who comes out ahead,
    so "favors_a" means the players team A receives (side B's players) are
    worth more than the ones it sends.

    Raises :class:`TradeValidationError` for anything unevaluable.
    """
    season = season if season is not None else current_nfl_season()
    rules = scoring.league_rules(league.settings_json)

    team_a_id, out_a = _side_input(side_a, "A")
    team_b_id, out_b = _side_input(side_b, "B")

    if team_a_id == team_b_id:
        raise TradeValidationError("A trade needs two different teams")

    overlap = sorted(set(out_a) & set(out_b))
    if overlap:
        raise TradeValidationError(
            f"Player(s) {', '.join(str(pid) for pid in overlap)} appear on both sides"
        )

    team_a = _load_team(db, league, team_a_id, "A")
    team_b = _load_team(db, league, team_b_id, "B")

    roster_a = set(_team_player_ids(db, league, team_a.id))
    roster_b = set(_team_player_ids(db, league, team_b.id))

    for label, team, roster, outgoing in (
        ("A", team_a, roster_a, out_a),
        ("B", team_b, roster_b, out_b),
    ):
        missing = [pid for pid in outgoing if pid not in roster]
        if missing:
            raise TradeValidationError(
                f"Player(s) {', '.join(str(pid) for pid in missing)} are not on "
                f"{team.name} (side {label})"
            )

    all_ids = roster_a | roster_b | set(out_a) | set(out_b)
    points, positions = _points_and_positions(db, league, sorted(all_ids), season)
    projections = _projections_for(db, season, sorted(all_ids))

    players = {
        player.id: player
        for player in db.query(Player).filter(Player.id.in_(sorted(all_ids))).all()
    }
    values: dict[str, dict[int, float]] = {"redraft": {}, "dynasty": {}}
    for row in (
        db.query(TradeValue)
        .filter(
            TradeValue.player_id.in_(sorted(all_ids)),
            TradeValue.source == TRADE_VALUE_SOURCE,
        )
        .all()
    ):
        if row.format in values:
            values[row.format][row.player_id] = row.value

    roster_slots = league_roster_slots(league)
    notes: list[str] = []
    missing_projection: list[str] = []
    missing_value: list[str] = []

    def side_payload(
        team: Team, roster: set[int], outgoing: list[int], incoming: list[int]
    ) -> dict:
        player_rows = []
        for pid in outgoing:
            player = players.get(pid)
            name = player.full_name if player else f"player {pid}"
            ros = points.get(pid, 0.0)
            if projections.get(pid) is None:
                missing_projection.append(name)
            if pid not in values["redraft"]:
                missing_value.append(name)
            player_rows.append(
                {
                    "player_id": pid,
                    "full_name": name,
                    "position": player.position if player else None,
                    "ros_points": round(ros, 2),
                    "ppg": round(ros / GAMES_PER_SEASON, 1),
                    "value": values["redraft"].get(pid),
                    "dynasty_value": values["dynasty"].get(pid),
                }
            )

        after = (roster - set(outgoing)) | set(incoming)
        before_points = round(
            sum(
                points.get(pid, 0.0)
                for _slot, pid in _fill_lineup(roster_slots, points, positions, roster)
            ),
            2,
        )
        after_points = round(
            sum(
                points.get(pid, 0.0)
                for _slot, pid in _fill_lineup(roster_slots, points, positions, after)
            ),
            2,
        )
        return {
            "team_id": team.id,
            "team_name": team.name,
            "players": player_rows,
            "ros_points_total": round(sum(r["ros_points"] for r in player_rows), 2),
            "value_total": round(
                sum(r["value"] or 0.0 for r in player_rows), 2
            ),
            "lineup_points_before": before_points,
            "lineup_points_after": after_points,
            "lineup_delta": round(after_points - before_points, 2),
        }

    payload_a = side_payload(team_a, roster_a, out_a, out_b)
    payload_b = side_payload(team_b, roster_b, out_b, out_a)

    verdict, margin_pct = _verdict(payload_a["value_total"], payload_b["value_total"])

    for payload in (payload_a, payload_b):
        notes.append(
            f"{payload['team_name']}'s optimal lineup changes by "
            f"{payload['lineup_delta']:+.1f} ROS points"
        )

    if league.is_keeper:
        dynasty_a = round(
            sum(values["dynasty"].get(pid, 0.0) for pid in out_a), 2
        )
        dynasty_b = round(
            sum(values["dynasty"].get(pid, 0.0) for pid in out_b), 2
        )
        dynasty_verdict, dynasty_margin = _verdict(dynasty_a, dynasty_b)
        if dynasty_verdict != verdict:
            notes.append(
                "Keeper league: on dynasty values this trade looks "
                f"{dynasty_verdict.replace('_', ' ')} "
                f"({dynasty_margin:.0%} margin), not {verdict.replace('_', ' ')}."
            )

    if missing_projection:
        notes.append(
            "No rest-of-season projection for "
            + ", ".join(sorted(set(missing_projection)))
            + "; their ROS points count as 0."
        )
    if missing_value:
        notes.append(
            "No market value for "
            + ", ".join(sorted(set(missing_value)))
            + "; the verdict may be unreliable."
        )

    return {
        "verdict": verdict,
        "margin_pct": margin_pct,
        "sides": {"a": payload_a, "b": payload_b},
        "notes": notes,
    }
