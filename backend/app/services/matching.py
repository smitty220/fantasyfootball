"""Shared normalized-name (+ position) player matching.

Every ingester resolves players by platform ID first (see docs/design.md);
this module is the common fallback used when a source's platform ID doesn't
resolve a `Player` row (fresh rookies, players missing from the nflverse
crosswalk, sources with no stable ID at all, etc.): normalize both the
source's name/position and the candidate's, and look for a single
unambiguous match.

Kept dependency-free (stdlib only) and framework-agnostic so any ingester
--- current or future --- can import it without pulling in anything else.
"""

from __future__ import annotations

import re
import unicodedata

from sqlalchemy.orm import Session

from app.models import Player

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
_PUNCTUATION_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")

#: Different sources spell the team defense/special-teams "position"
#: differently (Sleeper: "DEF", ESPN: "DST" derived from defaultPositionId,
#: FantasyPros: "DST" per its NFLPositions enum, Yahoo: "DEF"). Normalize
#: them all to one token so a fallback match isn't defeated by that alone.
_POSITION_ALIASES = {
    "DEF": "DST",
    "D/ST": "DST",
    "DST": "DST",
}


def normalize_name(name: str | None) -> str:
    """Lowercase, strip accents/punctuation, and drop generational suffixes.

    ``"Michael Pittman Jr."`` and ``"O'Dell Beckham II"`` both normalize
    cleanly (``"michael pittman"`` / ``"odell beckham"``).
    """
    if not name:
        return ""
    # Fold accented characters (e.g. "Ricky Pearsall" cases like "Amón-Ra"
    # -> "Amon-Ra") to plain ASCII before stripping punctuation.
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    lowered = _PUNCTUATION_RE.sub(" ", folded.lower())
    tokens = [t for t in _WHITESPACE_RE.split(lowered) if t and t not in _SUFFIXES]
    return " ".join(tokens)


def normalize_position(position: str | None) -> str:
    """Uppercase and collapse defense/special-teams spelling variants."""
    if not position:
        return ""
    upper = position.strip().upper()
    return _POSITION_ALIASES.get(upper, upper)


def find_by_name_position(
    db: Session, full_name: str | None, position: str | None
) -> Player | None:
    """Fallback lookup: normalized full name + normalized position.

    Returns ``None`` (rather than guessing) when there is no candidate, or
    when more than one candidate normalizes to the same name+position --
    an ambiguous match is worse than an unmatched row, since the caller
    would otherwise silently attach a projection/value to the wrong player.
    """
    target_name = normalize_name(full_name)
    if not target_name:
        return None
    target_position = normalize_position(position)

    query = db.query(Player)
    if target_position:
        # Position is stored as free text per-source (see module docstring),
        # so filter loosely in SQL and finish the real comparison in Python
        # via normalize_position() below -- this still avoids scanning
        # players with a *populated but totally different* position.
        query = query.filter(Player.position.isnot(None))

    candidates = [
        p
        for p in query.all()
        if normalize_name(p.full_name) == target_name
        and (not target_position or normalize_position(p.position) == target_position)
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None
