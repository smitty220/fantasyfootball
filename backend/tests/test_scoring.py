from app.services import scoring, stats


def test_standard_qb_line():
    rules = scoring.get_preset("standard")
    # 300 pass yds, 2 TD, 1 INT, 20 rush yds
    line = {"pass_yds": 300, "pass_td": 2, "pass_int": 1, "rush_yds": 20}
    assert scoring.score_stat_line(line, rules) == 300 * 0.04 + 8 - 1 + 2


def test_ppr_presets_differ_only_on_receptions():
    line = {"rec": 6, "rec_yds": 80, "rec_td": 1}
    std = scoring.score_stat_line(line, scoring.get_preset("standard"))
    half = scoring.score_stat_line(line, scoring.get_preset("half_ppr"))
    full = scoring.score_stat_line(line, scoring.get_preset("full_ppr"))
    assert half == std + 3
    assert full == std + 6


def test_dst_points_allowed_tiers():
    rules = scoring.get_preset("standard")
    assert scoring.score_stat_line({"dst_pts_allowed": 0}, rules) == 10
    assert scoring.score_stat_line({"dst_pts_allowed": 6}, rules) == 7
    assert scoring.score_stat_line({"dst_pts_allowed": 7}, rules) == 4
    assert scoring.score_stat_line({"dst_pts_allowed": 45}, rules) == -4


def test_unknown_stats_ignored():
    rules = scoring.get_preset("standard")
    assert scoring.score_stat_line({"nonsense": 99}, rules) == 0


def test_presets_are_copies():
    a = scoring.get_preset("standard")
    a["per_stat"]["rec"] = 99
    assert scoring.get_preset("standard")["per_stat"]["rec"] == 0.0


def test_league_rules_fallback():
    assert scoring.league_rules(None)["per_stat"]["rec"] == 0.5
    custom = {"scoring_rules": {"per_stat": {"rec": 2.0}}}
    assert scoring.league_rules(custom)["per_stat"]["rec"] == 2.0


def test_tier_scoring_normalizes_per_game():
    rules = scoring.get_preset("standard")
    # 332 pts allowed over 17 games = 19.5/gm -> 14-20 tier (1 pt) x 17 games
    season_line = {"dst_pts_allowed": 332}
    assert scoring.score_stat_line(season_line, rules, games=17) == 17.0
    # Same value read as a single game would land in the 35+ tier.
    assert scoring.score_stat_line(season_line, rules, games=1) == -4


def test_yds_allowed_tiers():
    rules = {
        "per_stat": {},
        "dst_yds_allowed_tiers": [[99, 3], [299, 1], [399, -1], [None, -1]],
    }
    assert scoring.score_stat_line({"dst_yds_allowed": 327}, rules) == -1
    assert scoring.score_stat_line({"dst_yds_allowed": 5568}, rules, games=17) == -17


# --------------------------------------------------------------------------- #
# Yahoo stat_modifiers -> scoring rules
# --------------------------------------------------------------------------- #

#: League Of Idiot Keepers' stat_modifiers as Yahoo returned them (2026-10-10).
KEEPER_MODIFIERS = {
    "stats": [
        {"stat": {"stat_id": sid, "value": value}}
        for sid, value in [
            (4, "0.05"), (5, "5"), (6, "-2"), (7, "-1"), (9, "0.1"), (10, "6"),
            (11, "1"), (12, "0.1"), (13, "5"), (14, "0.066666666666667"), (15, "6"),
            (16, "2"), (18, "-2"), (19, "3"), (20, "3"), (21, "3"), (22, "4"),
            (23, "5"), (24, "-1"), (25, "-1"), (26, "-1"), (27, "0"), (28, "0"),
            (29, "1"), (30, "-2"), (32, "1"), (33, "2"), (34, "1"), (35, "6"),
            (36, "2"), (37, "2"), (48, "0.066666666666667"), (49, "6"), (50, "10"),
            (51, "7"), (52, "4"), (53, "1"), (54, "0"), (55, "-2"), (56, "-2"),
            (57, "6"), (68, "1"), (70, "4"), (71, "3"), (72, "2"), (73, "1"),
            (74, "-1"), (75, "-1"), (76, "-1"), (77, "1"), (82, "2"),
        ]
    ]
}


def test_rules_from_yahoo_translates_the_keeper_league():
    rules = scoring.rules_from_yahoo(KEEPER_MODIFIERS)
    per_stat = rules["per_stat"]

    assert per_stat["rec"] == 1.0  # full PPR
    assert per_stat["pass_yds"] == 0.05  # 1 point per 20 yards
    assert per_stat["pass_td"] == 5 and per_stat["rec_td"] == 5
    assert per_stat["pass_int"] == -2 and per_stat["pass_sacked"] == -1
    assert per_stat["pass_2pt"] == per_stat["rush_2pt"] == per_stat["rec_2pt"] == 2
    assert per_stat["fg_50_plus"] == 5 and per_stat["xp_miss"] == -2
    assert per_stat["fg_made"] == 3 and per_stat["fg_miss"] == 0
    assert rules["dst_pts_allowed_tiers"] == [
        [0, 10], [6, 7], [13, 4], [20, 1], [27, 0], [34, -2], [None, -2]
    ]
    assert rules["dst_yds_allowed_tiers"][0] == [-1, 4]
    assert rules["dst_yds_allowed_tiers"][-1] == [None, -1]
    # Unprojectable Yahoo stats (TFL, three-and-outs, ...) add no keys.
    assert set(per_stat) <= stats.CANONICAL_STATS


def test_rules_from_yahoo_scores_a_stat_line_like_yahoo():
    rules = scoring.rules_from_yahoo(KEEPER_MODIFIERS)
    line = {"pass_yds": 300, "pass_td": 2, "pass_int": 1, "pass_sacked": 3, "rec": 4}
    # 15 + 10 - 2 - 3 + 4
    assert scoring.score_stat_line(line, rules) == 24.0


def test_rules_from_yahoo_without_kickers_or_defense():
    rules = scoring.rules_from_yahoo({"stats": [{"stat": {"stat_id": 11, "value": "1"}}]})
    assert rules == {"per_stat": {"rec": 1.0}}
