"""Evaluation engine: free-agent evaluator + trade evaluator.

Everything here reads only from SQLite (see design.md: evaluators never call
external APIs). The entry points are :func:`evaluate_free_agents`,
:func:`evaluate_trade` and :func:`team_lineup`; the helpers above them are
deliberately small and mostly pure so the maths can be unit-tested without a
database.

Core ideas
----------
*Rest-of-season points* come from the best available season-long projection
(``week IS NULL``) scored under the league's own rules, so the same player is
worth different amounts in the keeper league vs the redraft league.

*VOR* (value over replacement) subtracts a positional replacement level, which
is derived from how many starters the whole league needs at that position:
direct roster slots plus a share of each FLEX slot.

*Week points* come from the single-week projection rows (``week = N``) for
whatever week the ingestion last stored -- see :func:`current_projection_week`.

*Starters* come from the owner's saved lineup when one exists
(:func:`manual_lineup` -- ``RosterSlot`` rows stored under
:data:`MANUAL_LINEUP_WEEK`), and otherwise from the ROS-optimal lineup
(:func:`_fill_lineup` on ROS points). One starter set is used for every
comparison, weekly and ROS alike, so the "who would this pickup replace?"
answer never depends on which column you are looking at.

*Projection sources* are selectable. Every entry point takes an optional
``sources``: ``None`` keeps the historical behaviour (the best available source
in :data:`SOURCE_PRIORITY` order wins outright), while an explicit subset of
:data:`AVAILABLE_SOURCES` *averages* the selected sources -- see
:func:`_blend_points` for why the average is taken over league-scored points
rather than over raw stat lines. Whichever mode is in play, every number in a
response comes from it: replacement levels, VOR, optimal lineups, weekly and
ROS deltas and trade totals all read the same blended points.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, NamedTuple, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    League,
    LeaguePlayer,
    Player,
    Projection,
    RosterSlot,
    Team,
    TradeValue,
    TrendingSignal,
)
from app.services import scoring
from app.services.yahoo.sync import current_nfl_season

#: Projection sources we trust, best first.
SOURCE_PRIORITY: tuple[str, ...] = ("fantasypros", "espn")

#: Sources a caller may pick from, in :data:`SOURCE_PRIORITY` order.
AVAILABLE_SOURCES: tuple[str, ...] = SOURCE_PRIORITY

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

#: ``RosterSlot.week`` sentinel for an owner-chosen "standing" lineup. Yahoo's
#: real weekly rosters are stored under week >= 1, so week 0 can never collide
#: with a synced roster.
MANUAL_LINEUP_WEEK = 0

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


def validate_sources(raw: str | None) -> tuple[str, ...] | None:
    """Parse a ``"fantasypros,espn"`` selection into a source tuple.

    Case-insensitive and whitespace-tolerant; duplicates collapse and the
    result is ordered by :data:`AVAILABLE_SOURCES` so the same selection always
    reads back the same way. ``None``/empty means "no selection", i.e. the
    priority behaviour, and an unknown token raises :class:`ValueError` naming
    the offender.
    """
    if raw is None:
        return None
    tokens = [token.strip().lower() for token in raw.split(",")]
    tokens = [token for token in tokens if token]
    if not tokens:
        return None

    for token in tokens:
        if token not in AVAILABLE_SOURCES:
            raise ValueError(
                f"Unknown projection source '{token}'; choose from "
                + ", ".join(AVAILABLE_SOURCES)
            )
    chosen = set(tokens)
    return tuple(source for source in AVAILABLE_SOURCES if source in chosen)


def _selected_sources(sources: Sequence[str] | None) -> tuple[str, ...]:
    """The sources to *read* rows from: the selection, else every trusted one."""
    return tuple(sources) if sources else SOURCE_PRIORITY


def _blend_points(
    rows: Iterable[Projection],
    rules: scoring.ScoringRules,
    sources: Sequence[str] | None,
) -> float:
    """League points for one player from ``rows`` (all the same horizon).

    With no selection the single best row wins, exactly as it always has. With
    a selection every chosen source that *has* a row is scored under the
    league's rules and the results are averaged; a source with no row for this
    player is simply left out of that player's average rather than counted as
    zero, so a player only ESPN projects is not penalised for FantasyPros'
    silence.

    The average is taken over **league-scored points, not raw stats**: sources
    publish different stat granularities (one splits rushing and receiving
    touchdowns, another reports a single total; one carries return yards, the
    next does not), so averaging stat lines would blend fields that do not mean
    the same thing -- and would silently drop any stat a source omits. Points
    are the one quantity every source's line reduces to under the same rules,
    which makes them the comparable unit.
    """
    if not sources:
        return league_points(_pick_best(rows), rules)

    by_source = {row.source: row for row in rows}
    scored = [
        league_points(by_source[source], rules)
        for source in sources
        if source in by_source
    ]
    if not scored:
        return 0.0
    return sum(scored) / len(scored)


def league_points(
    projection: Projection | None, rules: scoring.ScoringRules
) -> float:
    """Fantasy points for a projection under one league's scoring rules."""
    if projection is None or not projection.stat_json:
        return 0.0
    return scoring.score_stat_line(projection.stat_json, rules)


def current_projection_week(
    db: Session, season: int, sources: Sequence[str] | None = None
) -> int | None:
    """The week our weekly projections currently describe, if any.

    Weekly ingestion only ever stores the *current* week (see
    ``espn.refresh_week_projections``), so the highest ``week`` on file is the
    latest one. ``None`` when the season has no weekly rows at all -- or, when
    ``sources`` narrows the selection, none from the chosen sources.
    """
    week = (
        db.query(func.max(Projection.week))
        .filter(
            Projection.season == season,
            Projection.week.isnot(None),
            Projection.source.in_(_selected_sources(sources)),
        )
        .scalar()
    )
    return int(week) if week is not None else None


def _projection_points(
    db: Session,
    season: int,
    rules: scoring.ScoringRules,
    player_ids: Sequence[int] | None = None,
    week: int | None = None,
    sources: Sequence[str] | None = None,
) -> dict[int, float]:
    """League-scored points per player, in one query.

    ``week=None`` means the season-long (``Projection.week IS NULL``) rows;
    ``week=N`` means that single week's rows. Players with no row from the
    selected sources are *absent* from the mapping rather than mapped to 0, so
    callers can tell "we have no forecast" from "we forecast nothing"; the ones
    that want a zero fill do it themselves.

    Multiple sources are combined by :func:`_blend_points`.
    """
    query = db.query(Projection).filter(
        Projection.season == season,
        Projection.week.is_(None) if week is None else Projection.week == week,
        Projection.source.in_(_selected_sources(sources)),
    )
    if player_ids is not None:
        ids = list({int(pid) for pid in player_ids})
        if not ids:
            return {}
        query = query.filter(Projection.player_id.in_(ids))

    grouped: dict[int, list[Projection]] = {}
    for row in query.all():
        grouped.setdefault(row.player_id, []).append(row)

    return {
        player_id: _blend_points(rows, rules, sources)
        for player_id, rows in grouped.items()
    }


def projection_sources(db: Session, season: int) -> dict:
    """What each selectable source has on file for ``season``.

    ``{"week", "sources"}``: the week the weekly rows describe (across every
    source, so the UI can label the column before a source is picked) and, per
    source in :data:`AVAILABLE_SOURCES`, when its season-long and current-week
    rows were last fetched. A source with nothing on file is still listed, with
    null timestamps, so the picker offers the same choices all season.
    """
    week = current_projection_week(db, season)

    def latest(source: str, week_value: int | None) -> Any:
        return (
            db.query(func.max(Projection.fetched_at))
            .filter(
                Projection.source == source,
                Projection.season == season,
                Projection.week.is_(None)
                if week_value is None
                else Projection.week == week_value,
            )
            .scalar()
        )

    return {
        "week": week,
        "sources": [
            {
                "source": source,
                "season_updated_at": latest(source, None),
                "week_updated_at": latest(source, week) if week is not None else None,
            }
            for source in AVAILABLE_SOURCES
        ],
    }


# --- roster shape ----------------------------------------------------------


def normalize_slot(slot: str) -> str:
    """Canonical slot label: upper-cased and de-aliased (``W/R/T`` -> ``FLEX``)."""
    label = (slot or "").strip().upper()
    return _SLOT_ALIASES.get(label, label)


def position_filter(position: str | None) -> list[str] | None:
    """Positions a ``position=`` query argument selects, or ``None`` for all.

    Understands slot labels as well as bare positions, so ``FLEX`` (and its
    ``W/R/T`` spellings) expands to RB/WR/TE and ``DST`` folds into ``DEF``.
    """
    if not position or not position.strip():
        return None
    label = normalize_slot(position)
    if label == "FLEX":
        return list(FLEX_POSITIONS)
    return [label]


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
        slot = normalize_slot(raw_slot)
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


def replacement_levels(
    db: Session,
    league: League,
    season: int | None = None,
    sources: Sequence[str] | None = None,
) -> dict[str, float]:
    """Points of the first non-startable player at each position.

    Every player with a projection (rostered or free agent) is ranked by league
    points; the replacement level is the player one spot past the last
    league-wide starter, i.e. rank ``ceil(starter_slots[pos] * num_teams) + 1``.

    A position with no projected players at all gets 0. If the pool is too
    shallow to reach that rank we use the worst projected player at the
    position rather than 0, which keeps VOR meaningful on tiny/partial pools.

    ``sources`` picks which projections the pool is ranked on, so VOR is quoted
    against a replacement priced the same way as the player it is subtracted
    from.
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
            Projection.source.in_(_selected_sources(sources)),
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
        pools[position].append(_blend_points(projections, rules, sources))

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


#: Order starters are displayed in.
STARTER_SLOT_ORDER: tuple[str, ...] = ("QB", "RB", "WR", "TE", "FLEX", "K", "DEF")


def _slot_rank(slot: str) -> int:
    try:
        return STARTER_SLOT_ORDER.index(slot)
    except ValueError:  # pragma: no cover - defensive; slots come from _fill_lineup
        return len(STARTER_SLOT_ORDER)


def starting_slot_counts(roster_slots: Mapping[str, int]) -> dict[str, int]:
    """How many instances of each *starting* slot the league has.

    Slot labels are normalized (``W/R/T`` -> ``FLEX``) and bench/IR slots are
    dropped, so ``{QB:1, "W/R/T":1, BN:6}`` becomes ``{QB:1, FLEX:1}``.
    """
    counts: dict[str, int] = {}
    for raw_slot, count in roster_slots.items():
        slot = normalize_slot(raw_slot)
        if slot in BENCH_SLOTS or not count:
            continue
        if slot == "FLEX" or slot in STARTABLE_POSITIONS:
            counts[slot] = counts.get(slot, 0) + int(count)
    return counts


def slot_instances(roster_slots: Mapping[str, int]) -> list[str]:
    """Every starting slot instance, in display order.

    ``{QB:1, RB:2, FLEX:1}`` becomes ``["QB", "RB", "RB", "FLEX"]`` -- one
    entry per seat in the lineup, which is what the UI renders.
    """
    counts = starting_slot_counts(roster_slots)
    instances: list[str] = []
    for slot in STARTER_SLOT_ORDER:
        instances.extend([slot] * counts.get(slot, 0))
    return instances


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

    counts = starting_slot_counts(roster_slots)

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
    sources: Sequence[str] | None = None,
) -> float:
    """Points of the best legal lineup buildable from ``player_ids``.

    Players without a projection are still eligible to fill a slot but
    contribute 0.
    """
    season = season if season is not None else current_nfl_season()
    points, positions = _points_and_positions(db, league, player_ids, season, sources)
    lineup = _fill_lineup(league_roster_slots(league), points, positions, player_ids)
    return round(sum(points.get(pid, 0.0) for _slot, pid in lineup), 2)


def _points_and_positions(
    db: Session,
    league: League,
    player_ids: Sequence[int],
    season: int,
    sources: Sequence[str] | None = None,
) -> tuple[dict[int, float], dict[int, str | None]]:
    """ROS points (zero-filled) and positions for ``player_ids``.

    The points mapping covers *every* requested id: a player with no season
    projection scores 0 here, because a lineup seat they occupy still has to be
    worth something.
    """
    ids = list({int(pid) for pid in player_ids})
    if not ids:
        return {}, {}
    rules = scoring.league_rules(league.settings_json)
    scored = _projection_points(db, season, rules, ids, sources=sources)
    points = {pid: scored.get(pid, 0.0) for pid in ids}
    positions = {
        player.id: player.position
        for player in db.query(Player).filter(Player.id.in_(ids)).all()
    }
    return points, positions


def _week_points(
    db: Session,
    league: League,
    player_ids: Sequence[int],
    season: int,
    week: int | None,
    sources: Sequence[str] | None = None,
) -> dict[int, float]:
    """League-scored points from the ``week`` projection, per player.

    Unlike ROS points, players with no weekly projection are simply *absent*
    from the mapping rather than scoring 0 -- callers surface that as a null,
    since "we have no forecast" and "we forecast nothing" are different
    claims for a single game.
    """
    ids = list({int(pid) for pid in player_ids})
    if not ids or week is None:
        return {}
    rules = scoring.league_rules(league.settings_json)
    return {
        pid: round(points, 2)
        for pid, points in _projection_points(
            db, season, rules, ids, week=week, sources=sources
        ).items()
    }


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


def manual_lineup(db: Session, team_id: int) -> dict[int, str] | None:
    """The owner's saved lineup for a team: ``player_id -> slot``.

    ``None`` when the team has no saved lineup at all, which is what makes the
    rest of the engine fall back to the computed optimal one. Slot labels are
    normalized, so a stored ``W/R/T`` reads back as ``FLEX``.
    """
    rows = (
        db.query(RosterSlot)
        .filter(
            RosterSlot.team_id == team_id,
            RosterSlot.week == MANUAL_LINEUP_WEEK,
        )
        .all()
    )
    if not rows:
        return None
    return {
        row.player_id: normalize_slot(row.selected_position or "") for row in rows
    }


def _manual_lineup_pairs(
    assignments: Mapping[int, str],
    roster_slots: Mapping[str, int],
    player_ids: Iterable[int],
    points: Mapping[int, float],
) -> list[tuple[str, int]]:
    """A saved lineup as ``(slot, player_id)`` pairs, in display order.

    Defensive against stale rows: assignments for players no longer on the
    roster, for slots the league does not have, and any overflow past a slot's
    capacity are dropped (best ROS points first) so the caller sees a lineup
    the league's roster shape can actually hold.
    """
    counts = starting_slot_counts(roster_slots)
    rostered = set(player_ids)

    candidates = [
        (slot, pid)
        for pid, slot in assignments.items()
        if pid in rostered and counts.get(slot)
    ]
    candidates.sort(
        key=lambda pair: (_slot_rank(pair[0]), -points.get(pair[1], 0.0), pair[1])
    )

    filled: dict[str, int] = {}
    lineup: list[tuple[str, int]] = []
    for slot, pid in candidates:
        if filled.get(slot, 0) >= counts[slot]:
            continue
        filled[slot] = filled.get(slot, 0) + 1
        lineup.append((slot, pid))
    return lineup


class TeamRoster(NamedTuple):
    """One team's players, its starting lineup, and both points views.

    The lineup is the owner's saved one when the team has one, and the
    *ROS-optimal* one otherwise (``source`` says which). Either way a single
    starter set drives both the ROS and the weekly comparisons, so the two
    never disagree about who is starting.
    """

    player_ids: list[int]
    lineup: list[tuple[str, int]]
    ros_points: dict[int, float]
    week_points: dict[int, float]
    positions: dict[int, str | None]
    source: str = "auto"

    @property
    def starter_slots(self) -> dict[int, str]:
        """player_id -> the slot they start in."""
        return {pid: slot for slot, pid in self.lineup}


def _team_roster(
    db: Session,
    league: League,
    team_id: int,
    season: int,
    week: int | None,
    sources: Sequence[str] | None = None,
) -> TeamRoster:
    ids = _team_player_ids(db, league, team_id)
    ros_points, positions = _points_and_positions(db, league, ids, season, sources)
    week_points = _week_points(db, league, ids, season, week, sources)
    roster_slots = league_roster_slots(league)

    assignments = manual_lineup(db, team_id)
    if assignments is not None:
        lineup = _manual_lineup_pairs(assignments, roster_slots, ids, ros_points)
        source = "manual"
    else:
        lineup = _fill_lineup(roster_slots, ros_points, positions, ids)
        source = "auto"
    return TeamRoster(ids, lineup, ros_points, week_points, positions, source)


def _worst_starters(
    roster: TeamRoster, points: Mapping[int, float]
) -> dict[str, float]:
    """Worst starter points per position, under one points view.

    The starter set is ``roster.lineup``: the owner's saved lineup when the
    team has one, the ROS-optimal lineup otherwise.

    FLEX-eligible positions (RB/WR/TE) all map to the same figure: the worst
    starter among the team's RB/WR/TE starters, because any of them could be
    the one a new FLEX-eligible player displaces.

    Starters missing from ``points`` (no weekly projection, say) are left out
    of the comparison entirely rather than counted as 0. A position with no
    starter at all is simply absent, which callers surface as a null delta --
    so a saved lineup that benches every RB, say, gives RB pickups no
    baseline to beat.
    """
    by_position: dict[str, list[float]] = {}
    flex_pool: list[float] = []
    for _slot, pid in roster.lineup:
        position = roster.positions.get(pid)
        value = points.get(pid)
        if position is None or value is None:
            continue
        by_position.setdefault(position, []).append(value)
        if position in FLEX_POSITIONS:
            flex_pool.append(value)

    worst = {
        position: min(values) for position, values in by_position.items() if values
    }
    if flex_pool:
        flex_worst = min(flex_pool)
        for position in FLEX_POSITIONS:
            worst[position] = flex_worst
    return worst


def _free_agent_players(
    db: Session, league: League, positions: Sequence[str] | None
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

    if positions:
        query = query.filter(Player.position.in_(list(positions)))
    return query.all()


def evaluate_free_agents(
    db: Session,
    league: League,
    position: str | None = None,
    limit: int = 50,
    season: int | None = None,
    sources: Sequence[str] | None = None,
) -> dict:
    """Rank a league's available players by value over replacement.

    Returns ``{"week", "rows", "my_players"}``: the week the weekly numbers
    describe (``None`` when no weekly projections are on file), the ranked
    free agents, and my own roster with its starters flagged so a pickup can
    be judged against the player it would actually replace.

    ``sources`` selects which projection sources every number here is built
    from -- ``None`` for the priority pick, a subset of
    :data:`AVAILABLE_SOURCES` to average them.
    """
    season = season if season is not None else current_nfl_season()
    rules = scoring.league_rules(league.settings_json)
    week = current_projection_week(db, season, sources)
    positions = position_filter(position)

    team = _my_team(db, league)
    my_roster = (
        _team_roster(db, league, team.id, season, week, sources)
        if team is not None
        else None
    )
    worst_ros = (
        _worst_starters(my_roster, my_roster.ros_points) if my_roster is not None else {}
    )
    worst_week = (
        _worst_starters(my_roster, my_roster.week_points) if my_roster is not None else {}
    )

    payload = {
        "week": week,
        "rows": [],
        "my_players": _my_player_rows(db, my_roster, positions),
    }

    players = _free_agent_players(db, league, positions)
    if not players:
        return payload

    player_ids = [player.id for player in players]
    projected = _projection_points(db, season, rules, player_ids, sources=sources)
    week_points = _week_points(db, league, player_ids, season, week, sources)
    levels = replacement_levels(db, league, season, sources)

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

    rows: list[dict] = []
    for player in players:
        ros_points = projected.get(player.id, 0.0)
        replacement = levels.get(player.position or "", 0.0)
        player_week_points = week_points.get(player.id)

        delta = None
        if player.position in worst_ros:
            delta = round(ros_points - worst_ros[player.position], 1)

        week_delta = None
        if player_week_points is not None and player.position in worst_week:
            week_delta = round(player_week_points - worst_week[player.position], 1)

        rows.append(
            {
                "player_id": player.id,
                "full_name": player.full_name,
                "position": player.position,
                "nfl_team": player.nfl_team,
                "injury_status": player.injury_status,
                "has_projection": player.id in projected,
                "ros_points": round(ros_points, 2),
                "ppg": round(ros_points / GAMES_PER_SEASON, 1),
                "week_points": player_week_points,
                "week_delta": week_delta,
                "vor": round(ros_points - replacement, 1),
                "trade_value": values.get(player.id),
                "trending_add": trending.get(player.id),
                "my_worst_starter_delta": delta,
            }
        )

    rows.sort(key=lambda row: (not row["has_projection"], -row["vor"], row["full_name"]))
    payload["rows"] = rows[:limit]
    return payload


# --- my roster / lineups ---------------------------------------------------


def _my_player_rows(
    db: Session, roster: TeamRoster | None, positions: Sequence[str] | None
) -> list[dict]:
    """My team's players, starters first (in slot order), then bench by ROS."""
    if roster is None or not roster.player_ids:
        return []

    query = db.query(Player).filter(Player.id.in_(roster.player_ids))
    if positions:
        query = query.filter(Player.position.in_(list(positions)))

    slots = roster.starter_slots
    rows = []
    for player in query.all():
        ros_points = roster.ros_points.get(player.id, 0.0)
        slot = slots.get(player.id)
        rows.append(
            {
                "player_id": player.id,
                "full_name": player.full_name,
                "position": player.position,
                "nfl_team": player.nfl_team,
                "injury_status": player.injury_status,
                "ros_points": round(ros_points, 2),
                "ppg": round(ros_points / GAMES_PER_SEASON, 1),
                "week_points": roster.week_points.get(player.id),
                "is_starter": slot is not None,
                "starter_slot": slot,
            }
        )

    rows.sort(
        key=lambda row: (
            not row["is_starter"],
            _slot_rank(row["starter_slot"]) if row["is_starter"] else 0,
            -row["ros_points"],
            row["full_name"],
        )
    )
    return rows


def team_lineup(
    db: Session,
    league: League,
    team_id: int,
    season: int | None = None,
    sources: Sequence[str] | None = None,
) -> dict:
    """One team's starting lineup, seat by seat, plus its bench.

    ``slots`` has one entry per seat the league's roster shape defines (two RB
    slots means two ``RB`` entries), in :data:`STARTER_SLOT_ORDER`. Seats are
    filled from the owner's saved lineup when the team has one
    (``source == "manual"``, and a seat the owner left empty comes back with a
    null ``player``), and from the ROS-optimal lineup otherwise
    (``source == "auto"``, which never leaves a seat empty while an eligible
    player is on the bench).

    Works for any team in the league, not just mine. ``sources`` selects which
    projections the seats -- and the points shown in them -- are computed from.
    """
    season = season if season is not None else current_nfl_season()
    week = current_projection_week(db, season, sources)
    roster = _team_roster(db, league, team_id, season, week, sources)

    players = {
        player.id: player
        for player in db.query(Player).filter(Player.id.in_(roster.player_ids)).all()
    }

    def row(pid: int) -> dict:
        player = players.get(pid)
        return {
            "player_id": pid,
            "full_name": player.full_name if player else f"player {pid}",
            "position": player.position if player else None,
            "nfl_team": player.nfl_team if player else None,
            "week_points": roster.week_points.get(pid),
            "ros_points": round(roster.ros_points.get(pid, 0.0), 2),
        }

    queues: dict[str, list[int]] = {}
    for slot, pid in roster.lineup:
        queues.setdefault(slot, []).append(pid)

    slots = []
    for slot in slot_instances(league_roster_slots(league)):
        queue = queues.get(slot)
        pid = queue.pop(0) if queue else None
        slots.append({"slot": slot, "player": row(pid) if pid is not None else None})

    started = {pid for _slot, pid in roster.lineup}
    bench = sorted(
        (row(pid) for pid in roster.player_ids if pid not in started),
        key=lambda entry: (-entry["ros_points"], entry["full_name"]),
    )
    return {"week": week, "source": roster.source, "slots": slots, "bench": bench}


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
    sources: Sequence[str] | None = None,
) -> dict:
    """Score a two-team trade.

    ``side_a``/``side_b`` are ``{"team_id": int, "player_ids": [int]}``: the
    players each side *sends away*. The verdict describes who comes out ahead,
    so "favors_a" means the players team A receives (side B's players) are
    worth more than the ones it sends.

    ``sources`` selects the projections behind every points figure: the per
    player ROS points, the side totals and both lineup deltas.

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
    players = {
        player.id: player
        for player in db.query(Player).filter(Player.id.in_(sorted(all_ids))).all()
    }
    # ``projected`` holds only the players some selected source actually
    # covers; ``points`` zero-fills the rest so lineups can still be built.
    projected = _projection_points(
        db, season, rules, sorted(all_ids), sources=sources
    )
    points = {pid: projected.get(pid, 0.0) for pid in all_ids}
    positions = {pid: player.position for pid, player in players.items()}
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
            if pid not in projected:
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
