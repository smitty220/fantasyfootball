"""Scoring engine: canonical stat lines -> fantasy points under league rules.

Rules are stored normalized in ``League.settings_json["scoring_rules"]``:

    {
        "per_stat": {"pass_yds": 0.04, "rec": 0.5, ...},   # canonical stat keys
        "dst_pts_allowed_tiers": [[0, 10], [6, 7], ...],   # [max_allowed, pts]
    }

Manual leagues write this shape directly (from a preset the owner can edit);
Yahoo leagues get it from :func:`rules_from_yahoo`, which the league sync
runs over Yahoo's stat_id-based ``stat_modifiers``.
Tier lists are ordered ascending by max; the last tier uses null/None as a
catch-all max.
"""

from __future__ import annotations

import copy
from typing import Any

ScoringRules = dict[str, Any]

#: Games a season-long stat line spans; used to per-game tier scoring.
GAMES_PER_SEASON = 17

# Typical Yahoo defaults; the manual-league UI offers these as editable presets.
_STANDARD: ScoringRules = {
    "per_stat": {
        "pass_yds": 0.04,
        "pass_td": 4,
        "pass_int": -1,
        "pass_2pt": 2,
        "rush_yds": 0.1,
        "rush_td": 6,
        "rush_2pt": 2,
        "rec": 0.0,
        "rec_yds": 0.1,
        "rec_td": 6,
        "rec_2pt": 2,
        "fum_lost": -2,
        "ret_td": 6,
        "fg_0_19": 3,
        "fg_20_29": 3,
        "fg_30_39": 3,
        "fg_40_49": 4,
        "fg_50_plus": 5,
        "fg_made": 3,
        "xp_made": 1,
        "dst_sack": 1,
        "dst_int": 2,
        "dst_fum_rec": 2,
        "dst_td": 6,
        "dst_safety": 2,
        "dst_blk": 2,
    },
    "dst_pts_allowed_tiers": [
        [0, 10],
        [6, 7],
        [13, 4],
        [20, 1],
        [27, 0],
        [34, -1],
        [None, -4],
    ],
}


def _with_rec(points_per_reception: float) -> ScoringRules:
    rules = copy.deepcopy(_STANDARD)
    rules["per_stat"]["rec"] = points_per_reception
    return rules


PRESETS: dict[str, ScoringRules] = {
    "standard": _STANDARD,
    "half_ppr": _with_rec(0.5),
    "full_ppr": _with_rec(1.0),
}

DEFAULT_ROSTER_SLOTS: dict[str, int] = {
    "QB": 1,
    "RB": 2,
    "WR": 3,
    "TE": 1,
    "FLEX": 1,
    "K": 1,
    "DEF": 1,
    "BN": 6,
}


def get_preset(name: str) -> ScoringRules:
    if name not in PRESETS:
        raise ValueError(f"Unknown scoring preset {name!r}; use one of {sorted(PRESETS)}")
    return copy.deepcopy(PRESETS[name])


#: Yahoo NFL stat_id -> the canonical per-stat keys it scores. One Yahoo stat
#: can feed several keys (a 2-point conversion counts however it was scored).
YAHOO_PER_STAT: dict[int, tuple[str, ...]] = {
    4: ("pass_yds",),
    5: ("pass_td",),
    6: ("pass_int",),
    7: ("pass_sacked",),
    9: ("rush_yds",),
    10: ("rush_td",),
    11: ("rec",),
    12: ("rec_yds",),
    13: ("rec_td",),
    14: ("ret_yds",),
    15: ("ret_td",),
    16: ("pass_2pt", "rush_2pt", "rec_2pt"),
    18: ("fum_lost",),
    19: ("fg_0_19",),
    20: ("fg_20_29",),
    21: ("fg_30_39",),
    22: ("fg_40_49",),
    23: ("fg_50_plus",),
    29: ("xp_made",),
    30: ("xp_miss",),
    32: ("dst_sack",),
    33: ("dst_int",),
    34: ("dst_fum_rec",),
    35: ("dst_td",),
    36: ("dst_safety",),
    37: ("dst_blk",),
}

#: Sources without a distance split project plain makes/misses; price them at
#: the bracket most kicks fall in (made: 30-39; missed: 40-49).
_YAHOO_FG_MADE_PROXY = 21
_YAHOO_FG_MISS_PROXY = 27

#: Yahoo points-allowed tiers, in ascending order: (stat_id, max allowed).
_YAHOO_PTS_ALLOWED_TIERS: tuple[tuple[int, int | None], ...] = (
    (50, 0), (51, 6), (52, 13), (53, 20), (54, 27), (55, 34), (56, None),
)

#: Yahoo yards-allowed tiers ("Neg" is anything below zero).
_YAHOO_YDS_ALLOWED_TIERS: tuple[tuple[int, int | None], ...] = (
    (70, -1), (71, 99), (72, 199), (73, 299), (74, 399), (75, 499), (76, None),
)


def yahoo_modifiers(stat_modifiers: dict | None) -> dict[int, float]:
    """``{stat_id: points}`` from Yahoo's ``stat_modifiers`` settings block."""
    modifiers: dict[int, float] = {}
    for entry in (stat_modifiers or {}).get("stats", []):
        stat = entry.get("stat", {}) if isinstance(entry, dict) else {}
        try:
            modifiers[int(stat["stat_id"])] = float(stat["value"])
        except (KeyError, TypeError, ValueError):
            continue
    return modifiers


def rules_from_yahoo(stat_modifiers: dict | None) -> ScoringRules:
    """Translate a Yahoo league's ``stat_modifiers`` into :data:`ScoringRules`.

    Stats Yahoo scores but no projection source projects (tackles for loss,
    three-and-outs, defensive return yards, ...) have no canonical key and
    are left out; they can't move a projection either way.
    """
    modifiers = yahoo_modifiers(stat_modifiers)
    per_stat: dict[str, float] = {}
    for stat_id, keys in YAHOO_PER_STAT.items():
        if stat_id in modifiers:
            for key in keys:
                per_stat[key] = modifiers[stat_id]
    if _YAHOO_FG_MADE_PROXY in modifiers:
        per_stat["fg_made"] = modifiers[_YAHOO_FG_MADE_PROXY]
    if _YAHOO_FG_MISS_PROXY in modifiers:
        per_stat["fg_miss"] = modifiers[_YAHOO_FG_MISS_PROXY]

    rules: ScoringRules = {"per_stat": per_stat}
    for rules_key, tiers in (
        ("dst_pts_allowed_tiers", _YAHOO_PTS_ALLOWED_TIERS),
        ("dst_yds_allowed_tiers", _YAHOO_YDS_ALLOWED_TIERS),
    ):
        if any(stat_id in modifiers for stat_id, _ in tiers):
            rules[rules_key] = [
                [max_allowed, modifiers.get(stat_id, 0.0)] for stat_id, max_allowed in tiers
            ]
    return rules


#: Tier-scored stats: (stat key in stat_json, rules key holding its tiers).
#: Tiers are per-GAME brackets, so multi-game stat lines are averaged first.
_TIER_STATS: tuple[tuple[str, str], ...] = (
    ("dst_pts_allowed", "dst_pts_allowed_tiers"),
    ("dst_yds_allowed", "dst_yds_allowed_tiers"),
)


def _tier_points(value: float, tiers: list) -> float:
    for max_allowed, tier_points in tiers:
        if max_allowed is None or value <= max_allowed:
            return float(tier_points)
    return 0.0


def score_stat_line(
    stat_json: dict[str, float], rules: ScoringRules, games: float = 1
) -> float:
    """Points for one canonical stat line under one league's rules.

    ``games`` says how many games the stat line spans (1 for a weekly line,
    ~17 for a season/rest-of-season line). Linear per-stat scoring is
    unaffected by it, but tier stats (points/yards allowed brackets) are
    per-game rules: the value is averaged per game, bracketed, and the tier
    points awarded once per game.
    """
    per_stat: dict[str, float] = rules.get("per_stat", {})
    points = 0.0
    for stat, value in stat_json.items():
        points += per_stat.get(stat, 0.0) * value

    games = max(float(games), 1.0)
    for stat_key, tiers_key in _TIER_STATS:
        tiers = rules.get(tiers_key)
        if tiers and stat_key in stat_json:
            per_game = stat_json[stat_key] / games
            points += _tier_points(per_game, tiers) * games

    return round(points, 2)


def league_rules(league_settings: dict | None) -> ScoringRules:
    """Pull normalized scoring rules from League.settings_json.

    Falls back to half-PPR when a league has no stored rules yet (e.g. Yahoo
    settings not synced and translator not run) so evaluators degrade to a
    sensible default rather than erroring.
    """
    if league_settings and "scoring_rules" in league_settings:
        return league_settings["scoring_rules"]
    return get_preset("half_ppr")
