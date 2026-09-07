"""Canonical stat vocabulary for projections.

Every projection source (FantasyPros, ESPN, Sleeper, ...) names stats
differently; each ingester must translate its source's names/IDs into these
keys before writing ``Projection.stat_json``. The scoring engine converts a
canonical stat line into points under a specific league's Yahoo scoring rules.

Keys not in CANONICAL_STATS must not appear in stat_json; ingesters should
drop unknown stats rather than invent new names (add them here first).
"""

from __future__ import annotations

CANONICAL_STATS: frozenset[str] = frozenset(
    {
        # passing
        "pass_att",
        "pass_cmp",
        "pass_yds",
        "pass_td",
        "pass_int",
        "pass_2pt",
        "pass_sacked",  # times sacked; only ESPN projects this today

        # rushing
        "rush_att",
        "rush_yds",
        "rush_td",
        "rush_2pt",
        # receiving
        "rec",
        "rec_yds",
        "rec_td",
        "rec_2pt",
        # misc offense
        "fum_lost",
        "ret_td",
        "ret_yds",  # kick/punt return yards; no current source projects these
        # kicking
        "fg_0_19",
        "fg_20_29",
        "fg_30_39",
        "fg_40_49",
        "fg_50_plus",
        "fg_made",  # only when a source gives no distance split
        "fg_miss",
        "xp_made",
        "xp_miss",
        # team defense / special teams
        "dst_sack",
        "dst_int",
        "dst_fum_rec",
        "dst_td",
        "dst_safety",
        "dst_blk",
        "dst_pts_allowed",
        "dst_yds_allowed",
        # some sources only give fantasy points, keep them for reference
        "fantasy_points",
    }
)


def clean_stat_line(raw: dict[str, float | int | None]) -> dict[str, float]:
    """Keep only canonical, non-null, non-zero stats; round to 2 decimals."""
    out: dict[str, float] = {}
    for key, value in raw.items():
        if key not in CANONICAL_STATS or value is None:
            continue
        value = round(float(value), 2)
        if value != 0:
            out[key] = value
    return out
