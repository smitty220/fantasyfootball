"""League standings computed from the ``Matchup`` table.

A manual league has no Yahoo standings to sync, so the owner's entered
matchup scores are the only record of who beat whom. :func:`league_standings`
turns those rows into the familiar W-L-T / points-for table, and writes the
same figures back onto the ``Team`` rows so the existing
``GET /api/leagues/{key}/teams`` endpoint (which reads ``Team.wins`` and
friends) shows them too.

Definitions used here, and why
------------------------------
*Final* means **both** ``home_points`` and ``away_points`` are non-null, not
``status == "final"``: status is a free-text column written by two different
producers (this app's matchup editor writes ``"final"``, the Yahoo sync writes
Yahoo's own vocabulary), while the presence of two scores means the same thing
no matter who wrote the row.

*Playoff rows are excluded.* Standings describe the regular season; a playoff
bracket result is not another regular-season win. ``is_playoffs`` defaults to
false, so this only ever matters for synced leagues.

*Byes are skipped.* ``away_team_id`` is nullable (an odd team count leaves one
team without an opponent); such a row is not a game and scores nothing.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import League, Matchup, Team


def final_matchups(db: Session, league: League) -> list[Matchup]:
    """The league's completed, non-playoff, two-team matchup rows.

    See the module docstring for what "completed" means and why the other two
    filters are here.
    """
    return (
        db.query(Matchup)
        .filter(
            Matchup.league_id == league.id,
            Matchup.is_playoffs.is_(False),
            Matchup.away_team_id.isnot(None),
            Matchup.home_points.isnot(None),
            Matchup.away_points.isnot(None),
        )
        .order_by(Matchup.week, Matchup.id)
        .all()
    )


def completed_weeks(db: Session, league: League) -> list[int]:
    """Weeks that have at least one final matchup, ascending."""
    return sorted({row.week for row in final_matchups(db, league)})


def league_standings(db: Session, league: League) -> list[dict]:
    """The league table, best first, from its final matchup rows.

    Sorted by wins descending, then points for descending (a tie counts as
    half a win for the ordering, exactly as it does for a record of 6-6-1).

    Side effect: every ``Team`` row in the league has its ``wins``/``losses``/
    ``ties``/``points_for``/``points_against``/``rank`` columns updated to
    match, and the session is committed -- *unless* the league has no final
    matchups at all, in which case the rows are left alone. That exception
    exists so calling this on a Yahoo league whose matchups have not been
    synced cannot wipe the standings the Yahoo sync wrote there.
    """
    teams = (
        db.query(Team)
        .filter(Team.league_id == league.id)
        .order_by(Team.id)
        .all()
    )
    if not teams:
        return []

    records: dict[int, dict] = {
        team.id: {
            "team_id": team.id,
            "name": team.name,
            "is_my_team": bool(team.is_my_team),
            "wins": 0,
            "losses": 0,
            "ties": 0,
            "points_for": 0.0,
            "points_against": 0.0,
            "games_played": 0,
        }
        for team in teams
    }

    rows = final_matchups(db, league)
    for row in rows:
        home = records.get(row.home_team_id)
        away = records.get(row.away_team_id)
        if home is None or away is None:
            # A matchup naming a team that has since been deleted.
            continue
        home_points = float(row.home_points)
        away_points = float(row.away_points)

        home["points_for"] += home_points
        home["points_against"] += away_points
        away["points_for"] += away_points
        away["points_against"] += home_points
        home["games_played"] += 1
        away["games_played"] += 1

        if home_points > away_points:
            home["wins"] += 1
            away["losses"] += 1
        elif away_points > home_points:
            away["wins"] += 1
            home["losses"] += 1
        else:
            home["ties"] += 1
            away["ties"] += 1

    table = sorted(
        records.values(),
        key=lambda row: (-(row["wins"] + 0.5 * row["ties"]), -row["points_for"]),
    )
    for row in table:
        row["points_for"] = round(row["points_for"], 2)
        row["points_against"] = round(row["points_against"], 2)

    if rows:
        by_id = {team.id: team for team in teams}
        for rank, row in enumerate(table, start=1):
            team = by_id[row["team_id"]]
            team.wins = row["wins"]
            team.losses = row["losses"]
            team.ties = row["ties"]
            team.points_for = row["points_for"]
            team.points_against = row["points_against"]
            team.rank = rank
        db.commit()

    return table
