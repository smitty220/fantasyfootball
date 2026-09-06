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
