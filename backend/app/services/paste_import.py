"""Import a manual league's teams/rosters/lineups from pasted Yahoo text.

Yahoo's "League > Rosters" page renders every team one after another, and a
plain copy/paste of it is the fastest way for an owner to stand a manual
league up (or refresh it after waivers) without the Yahoo API. The paste is
noisy -- each player shows up as a clean name line followed by a run of
scraped junk ("Lamar JacksonVideo ForecastPlayer Note", "Bal - QB", kickoff
time, note teasers) -- so :func:`parse_roster_paste` is deliberately a
tolerant, dependency-free state machine over the lines, and everything that
touches the database lives in :func:`import_roster_paste`.

The paste is authoritative for the teams it names: a team's roster is fully
replaced, and its saved week-0 lineup (see
:data:`app.services.evaluator.MANUAL_LINEUP_WEEK`) is rebuilt from the slot
labels. Teams the paste doesn't mention are left alone -- a partial paste of
two teams must never wipe the other ten.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import (
    League,
    LeaguePlayer,
    Player,
    Projection,
    RosterSlot,
    Team,
    TradeValue,
)
from app.services import evaluator, matching

# --- parsing ---------------------------------------------------------------

#: The column header Yahoo prints above every team's roster table. Finding one
#: is what starts a new team block.
_HEADER_RE = re.compile(r"^pos\s+player$", re.IGNORECASE)

#: The "Bal - QB" / "LAR - DEF" line inside a player block: NFL team, then the
#: player's real position, which we keep as a matching hint.
_TEAM_POS_RE = re.compile(r"^[A-Za-z]{2,4}\s*-\s*([A-Za-z/]{1,6})$", re.IGNORECASE)

#: Injured-reserve-ish slot labels ("IR", "IR-R", "PUP-R", "NFI/R", ...).
_IR_RE = re.compile(r"^(?:IR|PUP|NFI|SUSP)(?:[-/ ]?[A-Z]{1,3})?$")

#: Yahoo's placeholder for an unfilled roster slot.
_EMPTY_RE = re.compile(r"^\(?\s*empty\s*\)?$", re.IGNORECASE)

#: Slot labels (post-:func:`evaluator.normalize_slot`) that begin a player
#: block. Anything else on a line is treated as a name or as noise.
_KNOWN_SLOTS: frozenset[str] = frozenset(
    {"QB", "RB", "WR", "TE", "K", "DEF", "FLEX", "SUPERFLEX", "BN", "IR", "NA"}
)

#: Longest a line can be and still plausibly be a bare slot token.
_MAX_SLOT_LEN = 12


@dataclass
class ParsedPlayer:
    """One player as the paste describes them."""

    name: str
    #: Normalized slot label ("W/R/T" -> "FLEX", "IR-R" -> "IR").
    slot: str
    #: Position from the "Bal - QB" line, when the block had one.
    position_hint: str | None = None


@dataclass
class ParsedTeam:
    name: str
    players: list[ParsedPlayer] = field(default_factory=list)


def _slot_token(line: str) -> str | None:
    """The normalized slot this line names, or ``None`` if it isn't one."""
    label = line.strip().rstrip(":").strip().upper()
    if not label or len(label) > _MAX_SLOT_LEN:
        return None
    if label in ("NA", "TAXI", "TX"):
        return "NA"
    if _IR_RE.match(label):
        return "IR"
    slot = evaluator.normalize_slot(label)
    return slot if slot in _KNOWN_SLOTS else None


def _significant_lines(text: str) -> list[str]:
    """Stripped, non-empty lines, with a split ``Pos`` / ``Player`` header rejoined.

    Copying out of a browser sometimes breaks the two header cells onto their
    own lines; rejoining them here keeps the rest of the parser working off a
    single header pattern.
    """
    lines = [line.strip() for line in (text or "").splitlines()]
    lines = [line for line in lines if line]

    merged: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if (
            line.lower() == "pos"
            and index + 1 < len(lines)
            and lines[index + 1].lower() == "player"
        ):
            merged.append("Pos Player")
            index += 2
            continue
        merged.append(line)
        index += 1
    return merged


def parse_roster_paste(text: str) -> list[ParsedTeam]:
    """Teams and their players, as scraped from a Yahoo rosters-page paste.

    A ``Pos  Player`` line starts a team; the last unconsumed line before it is
    the team name (Yahoo prefixes the viewer's own team with ``"Your "``, which
    is kept verbatim -- stripping it here would be guessing, and team matching
    handles the prefix instead). Inside a team, a bare slot label starts a
    player block, the next line is the clean name, and every line after that is
    noise until the following slot label -- except the ``Bal - QB`` line, whose
    position is kept as a matching hint.

    Raises ``ValueError`` when the text contains no recognizable roster.
    """
    teams: list[ParsedTeam] = []
    current: ParsedTeam | None = None
    current_player: ParsedPlayer | None = None
    pending_slot: str | None = None
    #: Last line that was neither a slot label nor a captured player name --
    #: i.e. the running candidate for the next team's name.
    last_free: str | None = None

    for line in _significant_lines(text):
        if _HEADER_RE.match(line):
            current = ParsedTeam(
                name=sanitize_display_name(last_free or "") or f"Team {len(teams) + 1}"
            )
            teams.append(current)
            current_player = None
            pending_slot = None
            last_free = None
            continue

        slot = _slot_token(line)
        if slot is not None and current is not None:
            pending_slot = slot
            current_player = None
            continue

        if current is None:
            # Preamble junk ("Team", "Position", "Week 1", ...) before the
            # first roster; the last of it is the first team's name.
            last_free = line
            continue

        if pending_slot is not None:
            pending_slot_label, pending_slot = pending_slot, None
            if _EMPTY_RE.match(line):
                continue  # an unfilled slot contributes no player
            current_player = ParsedPlayer(
                name=sanitize_display_name(line), slot=pending_slot_label
            )
            current.players.append(current_player)
            continue

        match = _TEAM_POS_RE.match(line)
        if match is not None and current_player is not None:
            if current_player.position_hint is None:
                current_player.position_hint = match.group(1).upper()
        last_free = line

    if not teams:
        raise ValueError(
            "No teams found in the pasted text. Copy a Yahoo league rosters "
            "page -- each team needs its name followed by a 'Pos  Player' "
            "header and its slot/player rows."
        )
    return teams


# --- player matching -------------------------------------------------------


@dataclass
class _PlayerIndex:
    """Everything the matcher needs, loaded once per import."""

    by_name: dict[str, list[Player]]
    defenses: list[Player]
    valued_ids: set[int]
    projected_ids: set[int]


def _build_index(db: Session) -> _PlayerIndex:
    by_name: dict[str, list[Player]] = {}
    defenses: list[Player] = []
    for player in db.query(Player).all():
        by_name.setdefault(matching.normalize_name(player.full_name), []).append(player)
        if matching.normalize_position(player.position) == "DST":
            defenses.append(player)

    valued_ids = {
        row.player_id
        for row in db.query(TradeValue.player_id).filter(
            TradeValue.source == evaluator.TRADE_VALUE_SOURCE,
            TradeValue.format == "redraft",
        )
    }
    projected_ids = {row.player_id for row in db.query(Projection.player_id).distinct()}
    return _PlayerIndex(by_name, defenses, valued_ids, projected_ids)


def _nickname_candidates(normalized: str, defenses: Sequence[Player]) -> list[Player]:
    """Defenses whose full name contains the pasted nickname as whole words.

    Yahoo lists team defenses by nickname ("Eagles"); our rows come from
    Sleeper as "Philadelphia Eagles".
    """
    if not normalized:
        return []
    needle = f" {normalized} "
    return [
        player
        for player in defenses
        if needle in f" {matching.normalize_name(player.full_name)} "
    ]


def _preferred(candidates: list[Player], index: _PlayerIndex) -> Player | None:
    """Break a same-name tie, or give up rather than guess.

    A player carrying a FantasyCalc redraft value is the one the league cares
    about; failing that, one with any projection beats a bare row (duplicate
    names in the pool are usually a practice-squad namesake with no data).
    """
    for group in (index.valued_ids, index.projected_ids):
        narrowed = [player for player in candidates if player.id in group]
        if len(narrowed) == 1:
            return narrowed[0]
        if narrowed:
            candidates = narrowed
    return candidates[0] if len(candidates) == 1 else None


def _match_player(parsed: ParsedPlayer, index: _PlayerIndex) -> Player | None:
    """Resolve one pasted player to a ``Player`` row, or ``None``.

    Same normalized-name + normalized-position semantics as
    :func:`app.services.matching.find_by_name_position`, plus: the slot label
    stands in for a missing position hint, a hint that matches nothing falls
    back to a name-only match (Yahoo and our pool disagree about a position
    more often than they disagree about a name), and DEF entries match by
    nickname.
    """
    normalized = matching.normalize_name(parsed.name)
    if not normalized:
        return None

    hint = parsed.position_hint
    if not hint and parsed.slot in evaluator.STARTABLE_POSITIONS:
        hint = parsed.slot
    target_position = matching.normalize_position(hint)

    candidates = list(index.by_name.get(normalized, []))
    if target_position:
        filtered = [
            player
            for player in candidates
            if matching.normalize_position(player.position) == target_position
        ]
        if filtered:
            candidates = filtered
    if not candidates and target_position == "DST":
        candidates = _nickname_candidates(normalized, index.defenses)

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    return _preferred(candidates, index)


# --- team matching ---------------------------------------------------------


def _without_your_prefix(name: str) -> str:
    stripped = name.strip()
    if stripped.lower().startswith("your "):
        return stripped[5:].strip()
    return stripped


def sanitize_display_name(name: str) -> str:
    """Strip the junk Yahoo copies alongside a name.

    Real pastes carry trailing private-use icon glyphs (e.g. U+E037), other
    control/format characters, and stray whitespace: ``"Black Gold "``
    must equal ``"Black Gold"``.
    """
    cleaned = "".join(
        ch
        for ch in name
        if not (0xE000 <= ord(ch) <= 0xF8FF)  # private-use icon glyphs
        and unicodedata.category(ch) not in ("Cc", "Cf")
    )
    return re.sub(r"\s+", " ", cleaned).strip()


def _team_match_key(name: str) -> str:
    """Aggressively normalized form for team-name equality."""
    bare = _without_your_prefix(sanitize_display_name(name))
    return bare.replace("’", "'").casefold()


def _match_team(parsed_name: str, teams: Sequence[Team], taken: set[int]) -> Team | None:
    """Existing team for this pasted name: exact, then case-insensitive, then
    fully normalized (icon glyphs, "Your " prefix, curly quotes ignored)."""
    available = [team for team in teams if team.id not in taken]

    def first(predicate) -> Team | None:
        return next((team for team in available if predicate(team)), None)

    exact = first(lambda team: team.name.strip() == parsed_name.strip())
    if exact is not None:
        return exact

    lowered = parsed_name.strip().lower()
    insensitive = first(lambda team: team.name.strip().lower() == lowered)
    if insensitive is not None:
        return insensitive

    key = _team_match_key(parsed_name)
    return first(lambda team: _team_match_key(team.name) == key)


# --- lineup validation -----------------------------------------------------


def validate_lineup_assignments(
    db: Session, team: Team, league: League, assignments: Sequence[tuple[int, str]]
) -> list[tuple[int, str]]:
    """``(player_id, normalized slot)`` pairs, or ``ValueError`` explaining why not.

    Shared by the PUT lineup endpoint (which turns the ``ValueError`` into a
    400) and by this module's import (which skips that team's lineup and says
    so in the report). Partial lineups are fine -- an owner may fill three
    slots and leave the rest to be shown as empty seats -- but every assignment
    must name a player on this team, a slot this league actually has, and a
    position that slot accepts, with no slot over its capacity and no player
    used twice.
    """
    rostered = {
        row.player_id
        for row in db.query(LeaguePlayer.player_id).filter(
            LeaguePlayer.league_id == league.id,
            LeaguePlayer.on_team_id == team.id,
        )
    }
    capacity = evaluator.starting_slot_counts(evaluator.league_roster_slots(league))
    positions = {
        player.id: player.position
        for player in db.query(Player)
        .filter(Player.id.in_([player_id for player_id, _slot in assignments]))
        .all()
    }

    pairs: list[tuple[int, str]] = []
    seen: set[int] = set()
    used: dict[str, int] = {}

    for player_id, raw_slot in assignments:
        if player_id in seen:
            raise ValueError(f"Player {player_id} is assigned to more than one slot")
        seen.add(player_id)

        if player_id not in rostered:
            raise ValueError(f"Player {player_id} is not on {team.name}'s roster")

        slot = evaluator.normalize_slot(raw_slot)
        if not capacity.get(slot):
            available = ", ".join(
                s for s in evaluator.STARTER_SLOT_ORDER if capacity.get(s)
            )
            raise ValueError(
                f"{raw_slot!r} is not a starting slot in this league; "
                f"available slots: {available or 'none'}"
            )

        used[slot] = used.get(slot, 0) + 1
        if used[slot] > capacity[slot]:
            raise ValueError(
                f"This league has only {capacity[slot]} {slot} slot(s); "
                f"{used[slot]} players were assigned to {slot}"
            )

        position = positions.get(player_id)
        if slot == "FLEX":
            eligible: tuple[str, ...] = evaluator.FLEX_POSITIONS
        elif slot == "SUPERFLEX":
            eligible = evaluator.SUPERFLEX_POSITIONS
        else:
            eligible = (slot,)
        if position not in eligible:
            raise ValueError(
                f"Player {player_id} ({position or 'unknown position'}) "
                f"cannot start in a {slot} slot"
            )

        pairs.append((player_id, slot))

    return pairs


# --- import ----------------------------------------------------------------


def _clear_lineup(
    db: Session, team_id: int, player_ids: Sequence[int] | None = None
) -> None:
    """Drop week-0 lineup rows for a team (optionally only for some players)."""
    query = db.query(RosterSlot).filter(
        RosterSlot.team_id == team_id,
        RosterSlot.week == evaluator.MANUAL_LINEUP_WEEK,
    )
    if player_ids is not None:
        if not player_ids:
            return
        query = query.filter(RosterSlot.player_id.in_(list(player_ids)))
    query.delete(synchronize_session=False)


def import_roster_paste(db: Session, league: League, text: str) -> dict:
    """Sync ``league``'s teams, rosters and lineups from a rosters-page paste.

    Callers must have already established that ``league`` is a manual one (the
    router's guard does). Rosters are replaced, not merged; unmatched players
    and un-settable lineups are reported rather than aborting the import, since
    a paste with one unknown rookie in it is still worth 95% of the work.
    """
    parsed_teams = parse_roster_paste(text)
    index = _build_index(db)
    existing_teams = db.query(Team).filter(Team.league_id == league.id).all()

    # Wrong-league guard: a paste whose team names match NONE of an already
    # populated league's teams is almost certainly the other league's rosters
    # page. Creating ten duplicate teams (and stealing shared players from the
    # real ones) is far worse than asking the owner to double-check.
    if existing_teams:
        keys = {_team_match_key(team.name) for team in existing_teams}
        if not any(_team_match_key(parsed.name) in keys for parsed in parsed_teams):
            raise ValueError(
                "None of the pasted team names match this league's teams -- "
                "this looks like a different league's rosters page. Paste was "
                "not applied."
            )

    taken: set[int] = set()
    plans: list[dict] = []

    # Pass 1: resolve teams and players, and drop everyone the paste no longer
    # has on that team. Removals for *every* team happen before any addition so
    # that a player traded between two teams in the same paste lands correctly
    # whichever order the teams appear in.
    for parsed in parsed_teams:
        team = _match_team(parsed.name, existing_teams, taken)
        created = team is None
        if team is None:
            team = Team(team_key="", league_id=league.id, name=parsed.name.strip())
            db.add(team)
            db.flush()
            team.team_key = f"manual.{league.id}.t.{team.id}"
            existing_teams.append(team)
        taken.add(team.id)

        matched: list[tuple[Player, ParsedPlayer]] = []
        unmatched: list[str] = []
        seen_ids: set[int] = set()
        for parsed_player in parsed.players:
            player = _match_player(parsed_player, index)
            if player is None:
                unmatched.append(parsed_player.name)
                continue
            if player.id in seen_ids:
                continue  # the same player listed twice on one roster
            seen_ids.add(player.id)
            matched.append((player, parsed_player))

        prior_ids = {
            row.player_id
            for row in db.query(LeaguePlayer.player_id).filter(
                LeaguePlayer.league_id == league.id,
                LeaguePlayer.on_team_id == team.id,
            )
        }
        removed = prior_ids - seen_ids
        if removed:
            db.query(LeaguePlayer).filter(
                LeaguePlayer.league_id == league.id,
                LeaguePlayer.on_team_id == team.id,
                LeaguePlayer.player_id.in_(list(removed)),
            ).delete(synchronize_session=False)
            _clear_lineup(db, team.id, list(removed))

        plans.append(
            {
                "team": team,
                "created": created,
                "matched": matched,
                "unmatched": unmatched,
                "added": len(seen_ids - prior_ids),
                "removed": len(removed),
                "kept": len(seen_ids & prior_ids),
            }
        )
    db.flush()

    # Pass 2: attach everyone the paste does have, then rebuild lineups.
    reports: list[dict] = []
    for plan in plans:
        team: Team = plan["team"]
        for player, _parsed_player in plan["matched"]:
            league_player = (
                db.query(LeaguePlayer)
                .filter(
                    LeaguePlayer.league_id == league.id,
                    LeaguePlayer.player_id == player.id,
                )
                .one_or_none()
            )
            if league_player is None:
                db.add(
                    LeaguePlayer(
                        league_id=league.id,
                        player_id=player.id,
                        status="T",
                        on_team_id=team.id,
                    )
                )
                continue
            if league_player.on_team_id not in (None, team.id):
                # Came from a team this paste didn't cover: its old lineup
                # must not keep starting a player it no longer rosters.
                _clear_lineup(db, league_player.on_team_id, [player.id])
            league_player.status = "T"
            league_player.on_team_id = team.id
        db.flush()

        assignments = [
            (player.id, parsed_player.slot)
            for player, parsed_player in plan["matched"]
            if parsed_player.slot not in evaluator.BENCH_SLOTS
        ]
        lineup_set = False
        lineup_error: str | None = None
        if assignments:
            try:
                pairs = validate_lineup_assignments(db, team, league, assignments)
            except ValueError as exc:
                lineup_error = str(exc)
            else:
                _clear_lineup(db, team.id)
                for player_id, slot in pairs:
                    db.add(
                        RosterSlot(
                            team_id=team.id,
                            week=evaluator.MANUAL_LINEUP_WEEK,
                            player_id=player_id,
                            selected_position=slot,
                        )
                    )
                lineup_set = True
        else:
            _clear_lineup(db, team.id)

        reports.append(
            {
                "team": team.name,
                "created": plan["created"],
                "added": plan["added"],
                "removed": plan["removed"],
                "kept": plan["kept"],
                "lineup_set": lineup_set,
                "lineup_error": lineup_error,
                "unmatched": plan["unmatched"],
            }
        )

    db.commit()
    return {
        "teams": reports,
        "total_unmatched": sum(len(report["unmatched"]) for report in reports),
    }
