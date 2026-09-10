"""Trade finder: propose trades between my team and every rival in a league.

Where :func:`app.services.evaluator.evaluate_trade` scores a trade somebody
already thought of, this module *invents* them: it enumerates every small
package my team could swap with each opponent, scores them with the same
machinery the analyzer uses, and returns the handful that would actually
improve my starting lineup without making the other manager obviously worse
off.

How it stays fast
-----------------
Everything is loaded up front in a handful of queries -- every team's roster,
each rostered player's rest-of-season league points
(:func:`evaluator._projection_points`), positions and FantasyCalc redraft
values -- and the search itself is pure Python over those dictionaries. No
query runs inside the candidate loop.

The candidate space is still large (a 10-team league with 16-man rosters is
roughly 2,700 combinations *per opponent* if nothing is trimmed), so it is cut
down in three cheap ways before the expensive part -- rebuilding both teams'
optimal lineups with :func:`evaluator._fill_lineup` -- ever runs:

1. Only QB/RB/WR/TE are tradeable. Kickers and defenses are waiver-wire
   churn, and suggesting them is noise. They stay on the roster and keep
   filling their lineup seats, they are just never part of a package.
2. Two-player packages are drawn from each side's :data:`PACKAGE_POOL` most
   valuable tradeables. A package built from a team's 9th-best asset is not a
   trade anybody is proposing.
3. A combination whose two sides both have a fully known market value that
   differs by more than :data:`VALUE_PRUNE_MARGIN` is dropped unscored: it
   could never clear the fairness bar below, so there is no point building
   four lineups to find that out.

What survives is scored exactly as the analyzer would score it, and then
filtered on three conditions (see :func:`find_trades`).
"""

from __future__ import annotations

from itertools import combinations
from typing import Iterable, Mapping, NamedTuple, Sequence

from sqlalchemy.orm import Session

from app.models import League, LeaguePlayer, Player, Team, TradeValue
from app.services import evaluator, scoring
from app.services.yahoo.sync import current_nfl_season

#: Positions a suggestion may move. K/DEF are excluded on purpose -- see above.
TRADEABLE_POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE")

#: Pre-scoring prune: both sides' values known and further apart than this and
#: the combination cannot pass :data:`MARGIN_SLACK` * ``FAIR_MARGIN`` anyway.
VALUE_PRUNE_MARGIN = 0.25

#: How much looser than the analyzer's "fair" verdict a suggestion may be.
#: Slightly above 1.0 so *near*-fair ideas still surface -- the manager, not
#: the engine, decides whether a 12%-margin offer is worth sending.
MARGIN_SLACK = 1.2

#: A suggestion has to be worth making: my optimal lineup must gain at least
#: this many rest-of-season points.
MIN_MY_DELTA = 1.0

#: ...and the other manager must not be clearly worse off, or they would never
#: accept. A shade below zero because a sideways trade is still tradeable.
MIN_OPP_DELTA = -1.0

#: How many tradeables per side feed the two-player package search.
PACKAGE_POOL = 8

#: At most this many suggestions per opponent, so one lopsided rival cannot
#: crowd out every other idea in the league.
PER_OPPONENT_LIMIT = 4


class _Asset(NamedTuple):
    """One tradeable player, reduced to what the search actually ranks on."""

    player_id: int
    ros_points: float
    value: float | None


class _Candidate(NamedTuple):
    """A scored suggestion, before de-noising and ranking."""

    opponent: Team
    sends: tuple[int, ...]
    receives: tuple[int, ...]
    my_delta: float
    opp_delta: float
    margin: float
    sends_ros: float


def _margin(sent: float, received: float) -> float:
    """Fairness margin: how far apart two side totals are, as a fraction.

    Same shape as :func:`evaluator._verdict`'s margin -- the difference over
    the bigger side -- so a suggestion's ``value_margin_pct`` means exactly
    what the trade analyzer's ``margin_pct`` means.
    """
    biggest = max(sent, received)
    if biggest <= 0:
        return 0.0
    return abs(sent - received) / biggest


def _package_value(ids: Iterable[int], values: Mapping[int, float]) -> float | None:
    """A package's total market value, or ``None`` if any player has none.

    Deliberately not zero-filled: an unpriced player makes the *whole* side's
    value unknown rather than cheap, which is what keeps the value prune from
    throwing away trades involving a player FantasyCalc has not published.
    """
    total = 0.0
    for pid in ids:
        value = values.get(pid)
        if value is None:
            return None
        total += value
    return total


def _lineup_points(
    roster_slots: Mapping[str, int],
    points: Mapping[int, float],
    positions: Mapping[int, str | None],
    player_ids: Iterable[int],
) -> float:
    """Points of the best legal lineup from ``player_ids``, in memory.

    The same greedy fill :func:`evaluator.optimal_lineup_points` and
    :func:`evaluator.evaluate_trade` use, minus the database round trip.
    """
    return sum(
        points.get(pid, 0.0)
        for _slot, pid in evaluator._fill_lineup(
            roster_slots, points, positions, player_ids
        )
    )


def _pool_rank(asset: _Asset) -> tuple:
    """Sort key for "best assets first": priced players by price, then the rest.

    An unpriced player is not assumed worthless -- they simply sort behind
    every priced one, ordered among themselves by projection, because market
    value is the only quantity that compares a QB to a WR.
    """
    return (
        asset.value is None,
        -(asset.value or 0.0),
        -asset.ros_points,
        asset.player_id,
    )


def find_trades(
    db: Session,
    league: League,
    sources: Sequence[str] | None = None,
    limit: int = 10,
    season: int | None = None,
) -> dict:
    """Suggest trades between my team and every other team in ``league``.

    Returns ``{"my_team": {"id", "name"}, "suggestions": [...]}``. Each
    suggestion names the opponent, the players I would send and receive, both
    teams' optimal-lineup deltas in rest-of-season points, the market-value
    margin between the two sides and the shape of the deal (``"1for1"``,
    ``"2for1"`` -- I send two -- or ``"1for2"``).

    A candidate is kept only when all three hold:

    * the value margin is within ``FAIR_MARGIN * MARGIN_SLACK``, so the offer
      is at least arguable rather than an insult;
    * my optimal lineup gains at least :data:`MIN_MY_DELTA` points, so the
      trade is worth making at all;
    * the opponent's optimal lineup loses no more than ``-MIN_OPP_DELTA``
      points, because a rational manager does not accept a clear downgrade.

    ``sources`` selects the projections behind every points figure, exactly as
    it does elsewhere in the evaluator.

    Raises :class:`ValueError` when no team in the league is flagged as mine --
    without one there is nobody to trade *for*.
    """
    season = season if season is not None else current_nfl_season()
    rules = scoring.league_rules(league.settings_json)
    roster_slots = evaluator.league_roster_slots(league)

    teams = (
        db.query(Team).filter(Team.league_id == league.id).order_by(Team.id).all()
    )
    my_team = next((team for team in teams if team.is_my_team), None)
    if my_team is None:
        raise ValueError(
            f"No team in league {league.league_key} is marked as yours; "
            "set your team before asking for trade suggestions."
        )

    payload = {"my_team": {"id": my_team.id, "name": my_team.name}, "suggestions": []}

    rosters: dict[int, list[int]] = {team.id: [] for team in teams}
    for row in (
        db.query(LeaguePlayer)
        .filter(
            LeaguePlayer.league_id == league.id,
            LeaguePlayer.on_team_id.isnot(None),
        )
        .all()
    ):
        if row.on_team_id in rosters:
            rosters[row.on_team_id].append(row.player_id)

    all_ids = sorted({pid for ids in rosters.values() for pid in ids})
    if not all_ids:
        return payload

    players = {
        player.id: player
        for player in db.query(Player).filter(Player.id.in_(all_ids)).all()
    }
    # ``projected`` covers only the players a selected source actually has;
    # ``points`` zero-fills the rest so a lineup seat they hold still scores.
    projected = evaluator._projection_points(
        db, season, rules, all_ids, sources=sources
    )
    points = {pid: projected.get(pid, 0.0) for pid in all_ids}
    positions = {pid: player.position for pid, player in players.items()}
    values = {
        row.player_id: row.value
        for row in db.query(TradeValue)
        .filter(
            TradeValue.player_id.in_(all_ids),
            TradeValue.source == evaluator.TRADE_VALUE_SOURCE,
            TradeValue.format == "redraft",
        )
        .all()
    }

    def assets(team_id: int) -> list[_Asset]:
        """A team's tradeable players, best asset first.

        Players with neither a market value nor a projection are dropped here
        rather than in the scoring loop: with no price and no forecast there
        is nothing to say about them, and every candidate they appear in would
        be discarded anyway.
        """
        rows = [
            _Asset(pid, points.get(pid, 0.0), values.get(pid))
            for pid in rosters.get(team_id, ())
            if pid in players
            and positions.get(pid) in TRADEABLE_POSITIONS
            and (pid in values or pid in projected)
        ]
        rows.sort(key=_pool_rank)
        return rows

    baselines = {
        team.id: _lineup_points(roster_slots, points, positions, rosters[team.id])
        for team in teams
    }

    my_ids = frozenset(rosters[my_team.id])
    my_assets = assets(my_team.id)
    if not my_assets:
        return payload

    my_singles = [asset.player_id for asset in my_assets]
    my_pairs = list(combinations(my_singles[:PACKAGE_POOL], 2))
    my_before = baselines[my_team.id]

    fair_ceiling = evaluator.FAIR_MARGIN * MARGIN_SLACK
    kept: list[_Candidate] = []

    for opponent in teams:
        if opponent.id == my_team.id:
            continue
        opp_assets = assets(opponent.id)
        if not opp_assets:
            continue

        opp_ids = frozenset(rosters[opponent.id])
        opp_before = baselines[opponent.id]
        opp_singles = [asset.player_id for asset in opp_assets]
        opp_pairs = list(combinations(opp_singles[:PACKAGE_POOL], 2))

        combos: list[tuple[tuple[int, ...], tuple[int, ...]]] = []
        combos.extend(
            ((mine,), (theirs,)) for mine in my_singles for theirs in opp_singles
        )
        combos.extend((pair, (theirs,)) for pair in my_pairs for theirs in opp_singles)
        combos.extend(((mine,), pair) for mine in my_singles for pair in opp_pairs)

        found: list[_Candidate] = []
        for sends, receives in combos:
            sends_value = _package_value(sends, values)
            receives_value = _package_value(receives, values)
            priced = sends_value is not None and receives_value is not None
            if priced and _margin(sends_value, receives_value) > VALUE_PRUNE_MARGIN:
                continue

            sends_set = frozenset(sends)
            receives_set = frozenset(receives)
            my_after = _lineup_points(
                roster_slots,
                points,
                positions,
                (my_ids - sends_set) | receives_set,
            )
            my_delta = round(my_after - my_before, 1)
            if my_delta < MIN_MY_DELTA:
                continue

            opp_after = _lineup_points(
                roster_slots,
                points,
                positions,
                (opp_ids - receives_set) | sends_set,
            )
            opp_delta = round(opp_after - opp_before, 1)
            if opp_delta < MIN_OPP_DELTA:
                continue

            sends_ros = sum(points.get(pid, 0.0) for pid in sends)
            if priced and max(sends_value, receives_value) > 0:
                margin = _margin(sends_value, receives_value)
            else:
                margin = _margin(
                    sends_ros, sum(points.get(pid, 0.0) for pid in receives)
                )
            margin = round(margin, 3)
            if margin > fair_ceiling:
                continue

            found.append(
                _Candidate(
                    opponent, sends, receives, my_delta, opp_delta, margin, sends_ros
                )
            )

        survivors = sorted(_undominated(found), key=_rank)
        kept.extend(survivors[:PER_OPPONENT_LIMIT])

    def _player_row(pid: int) -> dict:
        return {
            "player_id": pid,
            "full_name": players[pid].full_name,
            "position": positions.get(pid),
            "ros_points": round(points.get(pid, 0.0), 2),
            "value": values.get(pid),
        }

    kept.sort(key=_rank)
    payload["suggestions"] = [
        {
            "opponent": {
                "team_id": candidate.opponent.id,
                "name": candidate.opponent.name,
            },
            "sends": [_player_row(pid) for pid in candidate.sends],
            "receives": [_player_row(pid) for pid in candidate.receives],
            "my_lineup_delta": candidate.my_delta,
            "opp_lineup_delta": candidate.opp_delta,
            "value_margin_pct": candidate.margin,
            "kind": f"{len(candidate.sends)}for{len(candidate.receives)}",
        }
        for candidate in kept[:limit]
    ]
    return payload


def _rank(candidate: _Candidate) -> tuple:
    """Total order over suggestions: best for me first, ties broken by name.

    Every component is a plain number or a tuple of player ids, so two runs
    over the same data always produce the same list -- there is no dependence
    on dictionary or query order anywhere in the ranking.
    """
    return (
        -candidate.my_delta,
        candidate.margin,
        -candidate.opp_delta,
        candidate.opponent.id,
        candidate.sends,
        candidate.receives,
    )


def _cost_rank(candidate: _Candidate) -> tuple:
    """Dominance order: biggest lineup gain first, cheapest package to break ties.

    Not :func:`_rank`. The dominance sweep below only ever compares a
    candidate against ones it has already accepted, so the sweep order decides
    which of two mutually-comparable packages survives -- and the one that
    should survive is the *cheaper* one, not the one that happens to price
    closer to fair. Sorting by margin here would let "my WR2 plus a throw-in
    for their RB1" beat the identical "my WR2 for their RB1".
    """
    return (
        -candidate.my_delta,
        candidate.sends_ros,
        len(candidate.sends),
        candidate.sends,
        candidate.receives,
    )


def _undominated(candidates: list[_Candidate]) -> list[_Candidate]:
    """Drop suggestions that another suggestion strictly beats.

    Two candidates are comparable when they bring back the *same* players from
    the same opponent; among those, one dominates another when it improves my
    lineup at least as much while giving up no more rest-of-season points.
    That is what makes "send the star" and "send the star plus a bench WR for
    the identical return" collapse to the one worth sending.

    Candidates are swept in :func:`_cost_rank` order, so an exact tie is
    resolved by that ordering rather than by both copies surviving.
    """
    survivors: list[_Candidate] = []
    for candidate in sorted(candidates, key=_cost_rank):
        if any(
            other.receives == candidate.receives
            and other.opponent.id == candidate.opponent.id
            and other.my_delta >= candidate.my_delta
            and other.sends_ros <= candidate.sends_ros
            for other in survivors
        ):
            continue
        survivors.append(candidate)
    return survivors
