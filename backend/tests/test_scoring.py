from app.services import scoring


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
