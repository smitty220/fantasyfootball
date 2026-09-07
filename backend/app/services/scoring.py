"""Scoring engine: canonical stat lines -> fantasy points under league rules.

Rules are stored normalized in ``League.settings_json["scoring_rules"]``:

    {
        "per_stat": {"pass_yds": 0.04, "rec": 0.5, ...},   # canonical stat keys
        "dst_pts_allowed_tiers": [[0, 10], [6, 7], ...],   # [max_allowed, pts]
    }

Manual leagues write this shape directly (from a preset the owner can edit);
Yahoo leagues get translated into it from Yahoo's stat_id-based settings once
real settings sync (translator lands with the Yahoo data, see design.md).
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
