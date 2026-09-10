"""Monte Carlo playoff odds for a league's remaining regular season.

The question this answers is "given what has already happened, how often does
each team reach the playoffs?". We take the standings so far as fact, simulate
every remaining regular-season week many times, and count how often each team
finishes in a playoff seed.

Modelling choices
-----------------
*Team strength.* Each team gets one Normal scoring model for the whole
simulation, fixed up front:

``mean = optimal_lineup_points(rest-of-season roster) / GAMES_PER_SEASON``

i.e. the team's best legal lineup, priced on rest-of-season projections and
divided down to a per-game figure. Using the *weekly* projections instead
would be more precise for the next week and useless for the twelve after it
(only the current week is ever on file -- see
``evaluator.current_projection_week``), and re-deriving a lineup per team per
week per sim would cost hundreds of thousands of database round trips. One
``optimal_lineup_points`` call per team, made once before any sampling, is the
whole database cost of a run.

``sigma = max(15.0, 0.22 * mean)`` -- fantasy weekly scores are roughly 20-25%
noisy around their projection, and the 15-point floor keeps a team with a thin
or unprojected roster from being modelled as a metronome that always loses.

*Schedule.* A remaining week that already has matchup rows entered uses those
pairings, so an owner who has typed in the rest of the season gets their real
schedule simulated. A week with nothing entered is paired at random per sim
(shuffle, then pair adjacent teams), which averages over schedules rather than
inventing one. With an odd number of teams one team is simply left out of that
week: no game, no win, no points -- never a free win.

*Ranking.* Seeds are wins, then points for, matching :mod:`app.services.standings`
(a tie counts as half a win for both teams). The top ``playoff_teams`` seeds
make the playoffs.
"""

from __future__ import annotations

import random
from typing import Sequence

from sqlalchemy.orm import Session

from app.models import League, LeaguePlayer, Matchup, Team
from app.services import standings as standings_service
from app.services.evaluator import GAMES_PER_SEASON, optimal_lineup_points

#: Regular-season length when the league has not said otherwise.
DEFAULT_REGULAR_SEASON_WEEKS = 14

#: Playoff field size when the league has not said otherwise.
DEFAULT_PLAYOFF_TEAMS = 6

#: Floor on a team's weekly standard deviation, in fantasy points.
MIN_SIGMA = 15.0

#: Weekly standard deviation as a fraction of a team's mean score.
SIGMA_FRACTION = 0.22


def league_config(league: League) -> tuple[int, int]:
    """``(regular_season_weeks, playoff_teams)`` for a league.

    Both are read out of ``settings_json`` with the module defaults, so a
    league created before these keys existed simulates a standard 14-week,
    6-team-playoff season.
    """
    settings = league.settings_json or {}

    def positive_int(key: str, default: int) -> int:
        try:
            value = int(settings.get(key) or default)
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    return (
        positive_int("regular_season_weeks", DEFAULT_REGULAR_SEASON_WEEKS),
        positive_int("playoff_teams", DEFAULT_PLAYOFF_TEAMS),
    )


def _team_player_ids(db: Session, league: League) -> dict[int, list[int]]:
    """``team_id -> rostered player ids`` for the whole league, in one query."""
    rosters: dict[int, list[int]] = {}
    for team_id, player_id in db.query(
        LeaguePlayer.on_team_id, LeaguePlayer.player_id
    ).filter(
        LeaguePlayer.league_id == league.id,
        LeaguePlayer.on_team_id.isnot(None),
    ):
        rosters.setdefault(team_id, []).append(player_id)
    return rosters


def _scoring_models(
    db: Session,
    league: League,
    teams: Sequence[Team],
    sources: Sequence[str] | None,
) -> tuple[list[float], list[float]]:
    """Per-team ``(means, sigmas)``, one ``optimal_lineup_points`` call each."""
    rosters = _team_player_ids(db, league)
    means: list[float] = []
    sigmas: list[float] = []
    for team in teams:
        ids = rosters.get(team.id, [])
        season_points = (
            optimal_lineup_points(db, league, ids, sources=sources) if ids else 0.0
        )
        mean = season_points / GAMES_PER_SEASON
        means.append(mean)
        sigmas.append(max(MIN_SIGMA, SIGMA_FRACTION * mean))
    return means, sigmas


def _entered_pairings(
    db: Session, league: League, weeks: Sequence[int], index: dict[int, int]
) -> dict[int, list[tuple[int, int]]]:
    """Pairings the owner has already entered for ``weeks``, as team indices.

    Only two-team rows count; a bye row (null ``away_team_id``) contributes no
    game, and a row naming a team that is no longer in the league is dropped.
    """
    if not weeks:
        return {}
    pairings: dict[int, list[tuple[int, int]]] = {}
    rows = (
        db.query(Matchup)
        .filter(
            Matchup.league_id == league.id,
            Matchup.week.in_(list(weeks)),
            Matchup.away_team_id.isnot(None),
        )
        .order_by(Matchup.week, Matchup.id)
        .all()
    )
    for row in rows:
        home = index.get(row.home_team_id)
        away = index.get(row.away_team_id)
        if home is None or away is None:
            continue
        pairings.setdefault(row.week, []).append((home, away))
    return pairings


def _random_pairs(order: list[int], rng: random.Random) -> list[tuple[int, int]]:
    """Shuffle ``order`` in place and pair adjacent entries.

    An odd team count leaves the last team unpaired: it plays nobody that week
    rather than being handed a bye win.
    """
    rng.shuffle(order)
    return [
        (order[i], order[i + 1]) for i in range(0, len(order) - 1, 2)
    ]


def simulate(
    db: Session,
    league: League,
    sims: int = 10000,
    sources: Sequence[str] | None = None,
    rng: random.Random | None = None,
) -> dict:
    """Monte Carlo playoff odds for every team in ``league``.

    Returns ``{sims, regular_season_weeks, playoff_teams, completed_weeks,
    teams}``; each team row carries its current record, its playoff
    probability, its average finishing seed and its chance of the 1 seed,
    sorted by playoff probability descending.

    ``rng`` makes a run reproducible -- pass a seeded :class:`random.Random`
    and the same database gives the same numbers every time.
    """
    rng = rng if rng is not None else random.Random()
    regular_season_weeks, playoff_teams = league_config(league)

    table = standings_service.league_standings(db, league)
    if not table:
        return {
            "sims": sims,
            "regular_season_weeks": regular_season_weeks,
            "playoff_teams": playoff_teams,
            "completed_weeks": [],
            "teams": [],
        }

    teams = {
        team.id: team
        for team in db.query(Team).filter(Team.league_id == league.id).all()
    }
    ordered = [teams[row["team_id"]] for row in table]
    index = {team.id: i for i, team in enumerate(ordered)}
    count = len(ordered)

    done = standings_service.completed_weeks(db, league)
    last_done = max(done) if done else 0
    remaining = list(range(last_done + 1, regular_season_weeks + 1))

    base_wins = [row["wins"] + 0.5 * row["ties"] for row in table]
    base_points = [row["points_for"] for row in table]

    means, sigmas = _scoring_models(db, league, ordered, sources)
    pairings = _entered_pairings(db, league, remaining, index)

    playoff_hits = [0] * count
    seed_totals = [0] * count
    seed_one_hits = [0] * count
    field = min(playoff_teams, count)

    gauss = rng.gauss
    all_indices = list(range(count))
    sims = max(0, int(sims))

    for _sim in range(sims):
        wins = list(base_wins)
        points = list(base_points)

        for week in remaining:
            games = pairings.get(week)
            if games is None:
                games = _random_pairs(all_indices, rng)
            for home, away in games:
                home_score = gauss(means[home], sigmas[home])
                away_score = gauss(means[away], sigmas[away])
                points[home] += home_score
                points[away] += away_score
                if home_score > away_score:
                    wins[home] += 1
                elif away_score > home_score:
                    wins[away] += 1
                else:  # pragma: no cover - a float tie is vanishingly rare
                    wins[home] += 0.5
                    wins[away] += 0.5

        finish = sorted(range(count), key=lambda i: (-wins[i], -points[i]))
        for seed, team_index in enumerate(finish, start=1):
            seed_totals[team_index] += seed
            if seed <= field:
                playoff_hits[team_index] += 1
            if seed == 1:
                seed_one_hits[team_index] += 1

    rows = []
    for i, row in enumerate(table):
        rows.append(
            {
                "team_id": row["team_id"],
                "name": row["name"],
                "is_my_team": row["is_my_team"],
                "current": {
                    "wins": row["wins"],
                    "losses": row["losses"],
                    "ties": row["ties"],
                    "points_for": row["points_for"],
                },
                "playoff_prob": round(playoff_hits[i] / sims, 3) if sims else 0.0,
                "avg_seed": round(seed_totals[i] / sims, 2) if sims else None,
                "seed_1_prob": round(seed_one_hits[i] / sims, 3) if sims else 0.0,
            }
        )
    rows.sort(key=lambda row: (-row["playoff_prob"], row["avg_seed"] or 0.0))

    return {
        "sims": sims,
        "regular_season_weeks": regular_season_weeks,
        "playoff_teams": playoff_teams,
        "completed_weeks": done,
        "teams": rows,
    }
